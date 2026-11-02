# Video Platform: Build Plan for Coding Agent

Nov 2, 2026 · @Maulik

## Overview

**Working name:** Videio (rename freely). A small video-sharing platform whose real subject is the backend: object storage, a job queue with transcoding workers, a replicated database, a cache layer, and a CI/CD pipeline that ships it to a VPS.

**For the coding agent:** build in the phase order at the end of this doc. Finish each phase, run its acceptance checks, commit on a feature branch, and stop for review before the next phase. Prefer boring, well-known libraries. Do not add features that are not listed here.

**Scope, deliberately small:**

1. Auth: register, log in, log out, refresh session. Two roles: `user` and `admin`.
2. Upload: browser uploads the original straight to object storage; workers transcode it to a fixed HLS ladder (360p, 720p) plus a thumbnail.
3. Watch: a feed of public videos, a video page with an HLS player, owner controls for visibility (public / unlisted / private), admin takedown.

**Out of scope:** comments, likes, subscriptions, search, recommendations, email verification, password reset, live streaming. Mention them in the README as "future work" only.

**Course requirements this must satisfy (Part B):** working web app, Dockerfile(s), GitHub repo with branches, PRs and real commit history, GitHub Actions pipeline that builds and tests on every push, public live link, README with setup, architecture and the link.

## Tech stack

Python (FastAPI) for the API, workers and scheduler, all in one package so they share models, config and database access. Plain JavaScript (JSX) for the frontend.

| Layer | Choice | Why |
| --- | --- | --- |
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async + asyncpg, uvicorn | Async, typed, OpenAPI generated from the models |
| Workers | taskiq + `taskiq-aio-pika` (RabbitMQ broker), `ffmpeg` in the image | Same codebase as the API, async tasks, retry middleware, separate container |
| Scheduler | `taskiq scheduler` with `LabelScheduleSource` | Cron-style periodic tasks kicked onto the same queue |
| Frontend | React + Vite, plain JavaScript (\`.jsx\`), ESLint with the React hooks plugin, `hls.js` for playback | Built to static files, served by the edge |
| Database | PostgreSQL 17 (official image): 1 primary, 1 streaming replica | Shows replication with plain Postgres, no extensions |
| Cache / counters / rate limits | Redis 7 (or Valkey 8), `redis.asyncio` | Cache-aside, atomic counters, token-bucket limits, refresh-token store |
| Queue | RabbitMQ 4 (management plugin on) | taskiq broker; delay queue and dead-letter queue; the UI is great for the demo |
| Object storage | SeaweedFS with its S3 gateway, accessed with `aioboto3` | Self-hosted, S3-compatible, Apache 2.0 |
| Edge | nginx container | Routing, HLS segment cache, `auth_request` for private videos, request rate limiting |
| TLS | Nginx Proxy Manager (already on the VPS) in front of the edge | Let's Encrypt certs from its UI |
| Tooling | `uv` for dependencies, `ruff` (lint + format), `mypy` | Fast, standard, easy in CI |
| Tests | pytest + pytest-asyncio + httpx `AsyncClient` against real Postgres/Redis; Vitest for frontend utils | Runs in CI on every push |
| Load test | k6 | Before/after numbers for caching and replicas |

**Do not use MinIO.** Its community edition stopped shipping Docker images in October 2025 and has been in maintenance mode since December 2025 ([InfoQ](https://infoq.com/news/2025/12/minio-s3-api-alternatives/), [It's FOSS](https://itsfoss.com/news/minio-moves-away-from-open-source/)). SeaweedFS is the closest drop-in. If the VPS is tight on RAM, Garage is the lighter alternative but needs a `garage.toml` and a one-time layout step ([comparison](https://blog.elest.io/minio-is-in-maintenance-mode-your-guide-to-s3-compatible-storage-alternatives/)). All storage access goes through one aioboto3 S3 client wrapper, so swapping backends is a config change.

**Do not use Bitnami images** for Postgres replication; use the official `postgres` image with a small init script (see Data model).

## Architecture

&#91;embedded content: architecture · 9 services on one VPS\]

Video bytes go from the browser straight to SeaweedFS and come back through the nginx edge cache; the API only signs URLs, writes metadata and publishes jobs. Workers and the scheduler are separate containers that scale independently of the API. Highlighted boxes are the async path that carries most of the design story.

| Service | Image | Replicas | Reached by |
| --- | --- | --- | --- |
| edge (nginx + SPA) | `videio-web` | 1 | NPM, app domain |
| api | `videio-api` | 2 | edge only |
| worker | `videio-worker` | 1–3 | nothing (consumes RabbitMQ) |
| scheduler | `videio-api` (scheduler command) | exactly 1 | nothing |
| migrate (one-shot) | `videio-api` | 0 after run | nothing |
| core-primary, core-replica | `postgres:17` | 1 each | internal network |
| redis | `redis:7-alpine` | 1 | internal network |
| rabbitmq | `rabbitmq:4-management` | 1 | internal; UI via SSH tunnel |
| seaweedfs | `chrislusf/seaweedfs` | 1 | NPM, `s3.` subdomain |

## System design concepts, mapped to features

Every concept below must exist in code, be visible in the demo, and be written up in `docs/design.md` with the trade-off it makes.

| Concept | Where it lives | How to show it in the demo |
| --- | --- | --- |
| Direct-to-storage upload (presigned multipart) | Browser PUTs parts straight to SeaweedFS; API only signs URLs | Network tab: upload bytes never touch the API |
| Async processing with a queue | `transcode_video` taskiq task on RabbitMQ, run by worker containers | RabbitMQ UI: messages ready / unacked during an upload |
| Horizontal worker scaling | `docker compose up --scale worker=3`; broker `qos=1` so each worker takes one job at a time | Upload 3 videos, watch them process in parallel |
| Retries with backoff + dead-letter queue | taskiq `SmartRetryMiddleware` (exponential backoff via the broker's delay queue), final failures to `transcode.dlq` | Upload a corrupt file; it lands in the DLQ, video shows FAILED |
| Idempotent consumers | Task keyed by `video_id`; status check before work; outputs written to deterministic keys | Re-kick the same task manually; no duplicate work |
| Read replica | Core DB: writes to primary, reads (session lookups, user profiles) to replica | `/admin/system` shows replication lag |
| Keyset pagination | Feed and My videos page by (created\_at, id) cursors, never OFFSET; feed reads from the replica | Scroll deep into the feed; response time stays flat |
| Cache-aside | Redis caches video metadata and the public feed | `X-Cache: HIT/MISS` header; k6 before/after |
| Cache invalidation | Owner edits or admin takedown delete the keys | Change visibility, refresh feed, it updates immediately |
| Write-behind counters | View counts `INCR` in Redis, flushed to Postgres every minute by a scheduled task | Counter jumps instantly, DB catches up |
| Edge caching | nginx `proxy_cache` on public HLS segments and thumbnails | `X-Edge-Cache: HIT` on second load |
| Authorization at the edge | nginx `auth_request` to the API for private video segments | Private video plays for owner, 403 for others even with the URL |
| Rate limiting | Redis token bucket per user on upload/login; nginx `limit_req` per IP | Spam login, get 429 with `Retry-After` |
| Stateless API, horizontally scalable | JWT access tokens; refresh tokens in Redis | Run 2 API replicas behind the edge |
| Health checks and graceful shutdown | `/healthz`, `/readyz`; workers finish the current job on SIGTERM | Redeploy during a transcode; job is not lost |

Be honest in the write-up: at this scale a read replica and several workers are for learning, not need. Say so, explain when a real system would add them, and explain why sharding was left out on purpose.

## Data model

One PostgreSQL database holds every table, with a primary for writes and a streaming replica for reads. **No sharding:** a few thousand video rows fit easily on one small instance, and sharding would add cross-shard queries, a routing layer and more containers for no gain. The design doc should say so, and name what would justify it later (write load or table size outgrowing one primary even after replicas and caching).

### Users and audit

| Table | Columns |
| --- | --- |
| `users` | `id uuid pk`, `email citext unique`, `password_hash text` (argon2id), `display_name`, `role` (`user`/`admin`), `created_at`, `disabled_at null` |
| `audit_log` | `id bigserial`, `actor_id`, `action` (e.g. `video.takedown`, `user.disable`), `target_type`, `target_id`, `reason`, `created_at` |

Refresh tokens live in Redis, not Postgres (key `rt:{tokenId}` → user id, TTL 7 days).

**Replication setup:** official `postgres:17` image. Primary sets `wal_level=replica`, `max_wal_senders=5`, creates a `replicator` role and a `pg_hba.conf` line for it. Replica's entrypoint script runs `pg_basebackup -h core-primary -D $PGDATA -R -X stream` when its data dir is empty, then starts in hot-standby mode.

**Routing in code:** two SQLAlchemy async engines, `db_write` (primary) and `db_read` (replica), exposed as FastAPI dependencies. Writes always go to primary; the feed, video pages and session lookups read from the replica. To avoid stale reads right after a write (read-your-writes), set a Redis flag `rw:{userId}` for 5 seconds after any write by that user; while it exists, that user's reads go to primary. Document this trade-off.

### Videos

| Table | Columns |
| --- | --- |
| `videos` | `id bigint identity pk, public_id char(11) unique`, `owner_id uuid`, `title`, `description`, `visibility` (`public`/`unlisted`/`private`), `status` (`pending_upload`/`uploaded`/`processing`/`ready`/`failed`), `original_key`, `duration_s`, `renditions jsonb`, `thumbnail_key`, `view_count bigint`, `failure_reason`, `created_at`, `updated_at`, `removed_at`, `removed_by`, `removal_reason` |
| `transcode_attempts` | `id`, `video_id`, `attempt`, `worker_id`, `started_at`, `finished_at`, `outcome`, `error` |

Indexes: `(owner_id, created_at desc)`, partial `(created_at desc) where visibility='public' and status='ready' and removed_at is null`.

**Public IDs:** URLs use `public_id`, 11 random base62 characters from Python's `secrets` module (about 65 bits), e.g. `/v/3kTq9bZx1`. Joins use the integer `id`. Random IDs also mean unlisted links can't be found by counting.

- **Public feed:** `WHERE visibility='public' AND status='ready' AND removed_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 20` on the replica, using the partial index. Cursor = last `(created_at, id)`, never `OFFSET`. Cache the first page in Redis for 30 s.
- **My videos:** same keyset pattern on `(owner_id, created_at desc)`; read from primary while the read-your-writes flag is set, so a just-uploaded video shows up.

### Migrations

Alembic, one environment in `backend/migrations`. A one-shot `migrate` service runs `alembic upgrade head` against the primary, then exits. CI runs the same command.

## Core flows

### Auth

1. Register: validate with Pydantic, hash with argon2id, insert into core primary. Rate limit 5/min per IP.
2. Login: verify hash, issue a 15-minute JWT access token and a 7-day opaque refresh token. Both go in `httpOnly`, `Secure`, `SameSite=Lax` cookies; the refresh cookie is scoped to `/api/auth/refresh`.
3. Refresh: rotate on every use. Old token is deleted from Redis; reuse of a deleted token revokes all of that user's tokens (theft detection).
4. Logout: delete the refresh token, clear cookies.
5. CSRF: cookies are `SameSite=Lax` and every mutating request must send `X-Requested-With: videio`; reject otherwise.
6. Cookies (not headers) are chosen on purpose so HLS segment requests carry identity automatically for `auth_request`.

### Upload

1. Client calls `POST /api/videos` with title, description, visibility, file size and MIME type. Limits: 500 MB, `video/mp4`, `video/quicktime`, `video/webm`, `video/x-matroska`. Per-user rate limit: 10 uploads/hour.
2. API creates the row on the primary (`status=pending_upload`), starts an S3 multipart upload in bucket `uploads` at key `originals/{videoId}`, returns presigned part URLs (10 MB parts) valid for 1 hour.
3. Browser uploads parts in parallel (3 at a time) with a progress bar, collects ETags.
4. Client calls `POST /api/videos/{id}/complete` with the ETags. API completes the multipart upload, sets `status=uploaded`, kicks `await transcode_video.kiq(video_id)`, returns 202.

### Transcode (worker)

1. `transcode_video(video_id: int)` is a task on the `AioPikaBroker` (`qos=1`, persistent messages, ack after the task finishes; check the installed taskiq version's ack setting and pick the "when executed" mode). Load the video; if status is `ready` or removed, return (idempotency).
2. Set `status=processing`, insert a `transcode_attempts` row.
3. Download original to a temp dir. `ffprobe` it: reject if no video stream or duration over 10 minutes (mark `failed`, no retry).
4. Produce a fixed HLS ladder with ffmpeg: 360p (\~800 kbps) and 720p (\~2800 kbps, skipped if the source is smaller), H.264 + AAC 128 kbps, 6-second segments, one `master.m3u8`. Thumbnail JPEG at 10% of duration. Run ffmpeg with `asyncio.create_subprocess_exec` and a 15-minute timeout so the event loop is never blocked.
5. Upload outputs to bucket `media` at `videos/{videoId}/...` (deterministic keys, so a retry overwrites rather than duplicates).
6. Set `status=ready`, save `renditions`, `duration_s`, `thumbnail_key`. Invalidate cache keys.
7. Transient errors (storage timeout, DB blip): raise. `SmartRetryMiddleware` re-kicks the task with exponential backoff and jitter (roughly 10 s, 60 s, 5 min), using the broker's delay queue; set `max_retries=3`. Permanent errors (bad input) are caught inside the task: mark `failed` and return without raising.
8. After the last retry: set `status=failed` with the reason and put the message in `transcode.dlq` (broker `dead_letter_queue_name`). If the installed `taskiq-aio-pika` version does not route final failures there by itself, add a small middleware whose `on_error` hook publishes to it on the final attempt. Verify this against the current taskiq docs before writing it.
9. On SIGTERM: `taskiq worker` stops receiving and waits for running tasks (`--wait-tasks-timeout 300`; set compose `stop_grace_period: 310s`). An unacked message returns to the queue if the worker dies.

### Scheduled tasks (taskiq scheduler, exactly one replica)

- `taskiq scheduler app.worker.scheduler:scheduler` with `LabelScheduleSource`; each periodic task declares `schedule=[{"cron": "..."}]`. The scheduler only kicks tasks onto the queue; workers run them. Run exactly one scheduler container, since a second one would kick every task twice.
- Every minute: flush view counters. `GETDEL views:{videoId}` for each id in the `views:dirty` set, `UPDATE videos SET view_count = view_count + n` in one batched statement.
- Every 5 min: re-kick jobs stuck in `processing` for over 20 minutes (the reaper).
- Every hour: delete rows stuck in `pending_upload` for 24 h and abort their multipart uploads.
- Each task takes a Redis lock (`SET lock:<task> NX PX 60000`) so overlapping runs do nothing.

### Playback

1. Video page calls `GET /api/videos/{id}` (cache-aside, key `video:{id}`, TTL 5 min) and checks access with the policy.
2. `hls.js` loads `/media/{id}/master.m3u8`. nginx sends an `auth_request` to `/internal/authz?video={id}`; the API answers 204 or 403 from cached visibility plus the cookie's user.
3. Public and unlisted segments are cached by nginx `proxy_cache` (7 days, segments are immutable). Private segments bypass the edge cache.
4. On first play, client calls `POST /api/videos/{id}/view`; API does `INCR views:{id}` and `SADD views:dirty {id}`. One view per user or IP per video per hour (Redis `SET NX EX`).

### RBAC

One pure function `can(actor, action, video)` in `backend/app/core/policy.py`, used by API routes and by `/internal/authz`. Unit-test it with this matrix (`pytest.mark.parametrize`):

| Action | Anonymous | Logged-in user | Owner | Admin |
| --- | --- | --- | --- | --- |
| View public / unlisted | Yes | Yes | Yes | Yes |
| View private | No | No | Yes | Yes |
| View removed | No | No | No (sees "removed" notice and reason) | Yes |
| Edit title, description, visibility | No | No | Yes | No |
| Delete own video | No | No | Yes | No |
| Take down / restore any video | No | No | No | Yes (reason required, written to `audit_log`) |
| Disable a user | No | No | No | Yes |

Unlisted videos are hidden from the feed but playable by link. Their random public IDs keep the links from being guessed.

## API surface

All JSON under `/api`. Errors use one shape via a custom exception handler: `{ "error": { "code": "VIDEO_NOT_FOUND", "message": "..." } }`. A middleware adds `X-Request-Id` to every response. FastAPI generates the OpenAPI spec from the Pydantic models; serve it at `/api/docs` (set `docs_url` and `openapi_url` under `/api`).

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/auth/register` | none | Create account |
| POST | `/api/auth/login` | none | Set session cookies |
| POST | `/api/auth/refresh` | refresh cookie | Rotate tokens |
| POST | `/api/auth/logout` | user | Revoke refresh token |
| GET | `/api/me` | user | Current user |
| GET | `/api/feed?cursor=` | none | Public feed from the replica, keyset-paginated, cached |
| GET | `/api/me/videos` | user | My videos, all statuses |
| POST | `/api/videos` | user | Create video + presigned part URLs |
| POST | `/api/videos/{id}/complete` | owner | Finish upload, kick transcode task |
| GET | `/api/videos/{id}` | policy | Metadata + status (client polls every 3 s while processing) |
| PATCH | `/api/videos/{id}` | owner | Title, description, visibility |
| DELETE | `/api/videos/{id}` | owner | Delete video and its objects |
| POST | `/api/videos/{id}/view` | none | Count a view |
| POST | `/api/admin/videos/{id}/takedown` | admin | Remove with reason |
| POST | `/api/admin/videos/{id}/restore` | admin | Undo takedown |
| POST | `/api/admin/users/{id}/disable` | admin | Disable account, revoke tokens |
| GET | `/api/admin/system` | admin | Queue depths and DLQ count (RabbitMQ management API), video counts by status, replica lag, cache hit ratio |
| GET | `/internal/authz?video=` | cookie | nginx `auth_request` only; not exposed publicly |
| GET | `/healthz`, `/readyz` | none (internal) | Liveness; readiness checks DBs, Redis, RabbitMQ |

## Repo, Docker, CI/CD and deployment

### Repo layout

```
videio/
  backend/                 one Python package, four entrypoints
    app/
      api/                 FastAPI routers: auth, videos, feed, admin, internal
      core/                config (pydantic-settings), security, policy.py,
                           ids.py (public IDs)
      db/                  engines, sessions, SQLAlchemy models
      services/            storage (aioboto3), cache, rate limits, counters
      worker/              broker.py, scheduler.py, ffmpeg.py,
                           tasks/transcode.py, tasks/maintenance.py
      main.py              FastAPI app factory
    migrations/               Alembic
    tests/unit/  tests/integration/  tests/e2e/
    pyproject.toml  uv.lock  Dockerfile
  web/                     React + Vite SPA; Dockerfile builds it into the edge image
  infra/
    nginx/                 edge config (routing, proxy_cache, auth_request, limit_req)
    postgres/              primary init (replication role, pg_hba), replica entrypoint
    seaweedfs/             s3.json identities, bucket + CORS init script
  load-tests/              k6 scripts
  docs/                    design.md, runbook.md, diagrams
  compose.yaml             local dev, full stack
  compose.ci.yaml          CI overrides
  compose.prod.yaml        VPS, pulls images from GHCR
  .github/workflows/       ci.yml, deploy.yml
  .github/pull_request_template.md
  .env.example
```

Entrypoints from the one backend codebase:

| Service | Command |
| --- | --- |
| api | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'` |
| worker | `taskiq worker app.worker.broker:broker app.worker.tasks --workers 1 --wait-tasks-timeout 300` |
| scheduler | `taskiq scheduler app.worker.scheduler:scheduler` |
| migrate | `python -m app.db.migrate` (alembic upgrade head) |

### Docker

- `backend/Dockerfile`, multi-stage: a `uv` builder stage installs from `uv.lock` into a venv; runtime is `python:3.12-slim-bookworm`. Two targets: `api` (also used by scheduler and migrate) and `worker` (adds `ffmpeg` via apt).
- `web/Dockerfile`: Node stage runs `vite build`, final stage is `nginx:1.27-alpine` with the SPA and the edge config baked in (it is the edge).
- Run as non-root, `HEALTHCHECK` in each image, `.dockerignore`, pinned base image tags, `init: true` in compose for signal handling.
- Images: `ghcr.io/<org>/videio-{api,worker,web}`, tagged `sha-<7 chars>` and `latest` on `main`.
- `migrate` runs from the api image; api waits for it with `depends_on: condition: service_completed_successfully`.
- No host ports in production. The edge and SeaweedFS join an external Docker network that Nginx Proxy Manager is also on (e.g. `npm_proxy`), so NPM reaches them by container name. Everything else stays on an internal network. RabbitMQ UI is reached by SSH tunnel.
- Resource limits in `compose.prod.yaml`: worker `cpus: 1.5`, `mem_limit: 1g`; Postgres instances `shared_buffers=64MB`. Target VPS: 4 GB RAM. On 2 GB, run one API replica and one worker.

### Presigned URLs gotcha (tell the agent explicitly)

SigV4 signatures include the host and path, so the browser must reach SeaweedFS at exactly the URL that was signed. Give storage its own subdomain, e.g. `s3.example.com`, as a separate NPM proxy host pointing at `videio-seaweedfs:8333`. Use path-style addressing. Sign with a client configured with the public endpoint (`https://s3.example.com`); use the internal endpoint for server-side reads and writes. Set bucket CORS to allow `PUT` from the app origin and expose the `ETag` header, or multipart completion fails silently.

**NPM proxy hosts (put in `docs/runbook.md`):**

| Domain | Forward to | SSL | Advanced tab |
| --- | --- | --- | --- |
| `app.example.com` | `videio-edge:80` | Let's Encrypt, force SSL, HTTP/2 | `client_max_body_size 10m;` |
| `s3.example.com` | `videio-seaweedfs:8333` | Let's Encrypt, force SSL | `client_max_body_size 0; proxy_request_buffering off; proxy_buffering off;` |

Because there are two proxies in a row (NPM, then the edge), configure the edge's `real_ip` module to trust the Docker network and read `X-Forwarded-For`, and run uvicorn with `--proxy-headers`. Otherwise every request looks like it came from NPM and per-IP rate limits break.

### CI: `.github/workflows/ci.yml` (every push and every PR)

1. **backend-lint:** `uv sync --frozen` with cache, `ruff check`, `ruff format --check`, `mypy app`.
2. **web-lint:** pnpm install with cache, ESLint, `Prettier check`, `vite build`.
3. **unit-tests:** `pytest tests/unit` for policy matrix, public ID generation, cursor encoding, ffmpeg argument builder.
4. **integration-tests:** `docker compose -f compose.yaml -f compose.ci.yaml up -d --wait`, run migrations, `pytest tests/integration` against real Postgres, Redis, RabbitMQ, SeaweedFS.
5. **e2e-smoke:** generate a 3-second test clip in CI (`ffmpeg -f lavfi -i testsrc=duration=3:size=640x360 ...`), upload it through the real API, poll until `ready`, fetch `master.m3u8` through the edge. Upload a junk file and assert it ends `failed` and appears in the DLQ.
6. **build-images:** `docker/build-push-action` with GHA layer cache for the three images. Push only on `main`.

### CD: `.github/workflows/deploy.yml` (on CI success for `main`, plus manual `workflow_dispatch` with an image tag input for rollback)

Use a GitHub Environment named `production` holding the secrets. Support both routes and pick one in the README:

- **SSH route (default):** `appleboy/ssh-action` runs `cd /opt/videio && IMAGE_TAG=sha-xxxxxxx docker compose -f compose.prod.yaml pull && docker compose -f compose.prod.yaml up -d --remove-orphans`.
- **Portainer route:** set the stack to pull from GHCR and `curl -X POST` the stack's webhook URL from a secret.

After either, a smoke job curls `https://<domain>/readyz` with retries and fails the workflow if it is not 200. Rollback = rerun `deploy.yml` with the previous `sha-` tag.

VPS one-time setup goes in `docs/runbook.md`: Docker and Nginx Proxy Manager already running; create `/opt/videio/.env` from `.env.example`; `docker login ghcr.io` with a read-only token; make sure the shared NPM network exists; add DNS records for the app and `s3.` subdomains; add the two NPM proxy hosts above.

## Frontend look and feel

The UI should read like an internal tool built by engineers who care: plain, dense, consistent. What makes generated UIs look generated is decoration, so avoid it.

**Pages (six, no more):** Feed, Video, Upload, My videos, Login/Register, Admin (videos table + system panel).

**Do:**

- One neutral palette (zinc/stone greys) plus one accent colour used only for primary actions and the active nav item. Support dark mode via `prefers-color-scheme`.
- System font stack or Inter; 14 px base; tight, consistent spacing on a 4 px scale.
- Real copy written for this product: "Processing — 720p will be ready in a minute or two", not "Unleash your creativity".
- Visible system state: status badges (`Uploading 43%`, `Processing`, `Ready`, `Failed: no video stream`), relative timestamps, view counts.
- A thumbnail grid on the feed; a 16:9 player with a quality selector (from HLS levels) on the video page.
- Empty states and error states for every list and form. Keyboard-focus styles.
- Admin "System" panel showing live numbers from `/api/admin/system`: queue ready/unacked, DLQ size, videos by status, replica lag, cache hit ratio. This doubles as your demo dashboard.

**Don't:** emoji in UI or commit messages, gradient hero banners, glassmorphism, oversized rounded cards with drop shadows, stock illustrations, a marketing landing page, lorem ipsum, "Welcome back! 👋".

**Implementation:** plain CSS modules or Tailwind with a small custom theme; no heavy component kit. React Router, TanStack Query for fetching and polling.

## Build order for the agent

Deploy in phase 0, not at the end: the pipeline is half the marks, and every later phase then ships through it. Each phase = one or more feature branches merged by PR, CI green before merge.

1. **Phase 0 — Skeleton and pipeline.** `backend/` with uv, FastAPI app with `/healthz`, pydantic-settings config; `web/` with a placeholder page; edge nginx; one Postgres; Dockerfiles; `compose.yaml`; `ci.yml` (ruff, mypy, one pytest, web build, image build); `deploy.yml`; NPM proxy host for the app domain.
   - Done when: a push to `main` goes green, images appear in GHCR, and the live URL shows the placeholder over HTTPS through NPM.
2. **Phase 1 — Auth and core replication.** Core primary + replica, Alembic core env, register/login/refresh/logout, cookies, CSRF header, login rate limit, read/write engine routing with read-your-writes flag.
   - Done when: integration tests cover the auth flow and token-reuse revocation; `/api/admin/system` shows replica lag.
3. **Phase 2 — Upload.** Video tables and public IDs, SeaweedFS with buckets and CORS, `s3.` NPM proxy host, presigned multipart upload with progress, `complete` endpoint, My videos page.
   - Done when: a 200 MB file uploads from the browser without passing through the API, and the video moves from pending\_upload to uploaded.
4. **Phase 3 — taskiq workers and playback.** `AioPikaBroker` with delay queue and DLQ, `SmartRetryMiddleware`, `transcode_video` task with ffprobe/ffmpeg, idempotency, graceful shutdown; taskiq scheduler with flush, reaper and cleanup tasks; video page with hls.js; feed with keyset pagination from the replica.
   - Done when: e2e smoke test in CI passes; a corrupt upload ends in the DLQ as `failed`; `--scale worker=3` processes three uploads in parallel.
5. **Phase 4 — RBAC and private media.** `policy.py` with the matrix tests, visibility editing, owner delete (removes objects too), admin takedown/restore/disable with `audit_log`, nginx `auth_request` on `/media/`, removed-video notice.
   - Done when: a private video's `master.m3u8` URL returns 403 when opened logged-out or as another user, 200 for owner and admin.
6. **Phase 5 — Caching, counters, limits, load tests.** Cache-aside for video and feed with invalidation on every write path, `X-Cache` header, view counters with write-behind flush and dedupe, per-user upload limits, nginx `proxy_cache` for public segments and `limit_req`, k6 scripts for feed, video page and upload.
   - Done when: k6 shows the before/after on `/api/feed` and `/api/videos/{id}` (record numbers in `docs/design.md`); changing visibility reflects in the feed within one request.
7. **Phase 6 — Docs and polish.** Structured JSON logs with request IDs (plain stdout, no metrics stack), README, `docs/design.md` (each concept: what, why, trade-off, how to see it), `docs/runbook.md` (deploy, rollback, DLQ replay, add a worker, promote the replica, NPM setup), seed script with a demo admin and a few sample videos, UI empty/error states.
   - Done when: a teammate can clone, run `cp .env.example .env && docker compose up`, and reach a working app at `http://localhost:8080` with no other steps.

## Team workflow, README and demo

### Git workflow

- Protect `main`: PRs only, CI must pass, 1 approving review from a teammate.
- Branches: `feat/<area>-<thing>`, `fix/...`, `chore/...`, `docs/...`. Short-lived, one concern each.
- Conventional Commits (`feat(worker): add retry queues with TTL backoff`). Small commits, each building.
- Split ownership so each person's work shows up under their own name in history and PR reviews, and each can explain their part in a viva: **A** — auth, core DB, replication, RBAC; **B** — storage, queue, worker, scheduler; **C** — pipeline, Docker, deployment, edge nginx, Nginx Proxy Manager setup. Frontend pages split by the feature each person owns.
- Run the coding agent from each owner's own machine and branch, review its diff before committing, and open PRs with the template (what, why, how tested, screenshots).

### README outline

1. One-paragraph summary + **live link** + CI badge.
2. Architecture diagram (export the one in this doc) and a short paragraph per component.
3. Local setup: prerequisites, `cp .env.example .env`, `docker compose up`, seeded demo accounts.
4. Running tests.
5. CI/CD: what each workflow does, how deploys and rollbacks work.
6. System design highlights: one line each, linking to `docs/design.md`.
7. Load-test results table.
8. Team and ownership; future work.

### 5-minute demo script

1. Show the GitHub repo: protected `main`, merged PRs, Actions run with all jobs green, image in GHCR.
2. Push a one-line change, watch it deploy, refresh the live site.
3. Upload a video; show the RabbitMQ UI while it processes; watch status flip to Ready; play it and switch quality.
4. Scale workers to 3 and upload three files at once.
5. Upload a corrupt file; show it in the DLQ and as Failed in the UI.
6. Make a video private; open its playlist URL in a private window, get 403.
7. Admin takes down a video with a reason; show the audit log.
8. Admin System panel: replica lag, queue depth, cache hit ratio; reload the feed twice to show `X-Cache: HIT`.
9. Show the k6 before/after table from `docs/design.md`.

### Course rubric checklist

- [ ] Working web app at a public HTTPS link
- [ ] Dockerfile per service; whole stack runs with `docker compose up`
- [ ] GitHub repo with feature branches, reviewed PRs, meaningful commit history from all three members
- [ ] GitHub Actions builds and tests on every push and PR
- [ ] Automatic deploy to the VPS from `main`, with smoke check and rollback
- [ ] README with setup, architecture and the live link
