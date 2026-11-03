# Videio

Videio is a small video-sharing platform whose real subject is the backend:
object storage, a job queue with transcoding workers, a replicated database, a
cache layer and a CI/CD pipeline that ships it to a VPS.

Live deployment: <https://videio.mauliksharma.org>

## Current build status

- Phase 0: skeleton, Docker images, edge routing and CI/CD.
- Phase 1: PostgreSQL primary/replica routing, Redis sessions, auth cookies,
  refresh-token reuse detection, CSRF protection and rate limits.
- Phase 2: direct SeaweedFS multipart uploads, video metadata, public IDs and
  the My videos page.
- Phase 3: taskiq workers on RabbitMQ transcode uploads to an HLS ladder
  (144p up to the original resolution, at most 2160p) with retries, backoff
  and a dead-letter queue; a single scheduler runs the view
  counter flush, the stuck-job reaper and abandoned-upload cleanup; the public
  feed and the video page with an HLS player.

Private-media authorization, admin moderation, caching and load tests are
planned for later phases.

## Run locally

Prerequisites: Docker Desktop with Compose and Node.js 22 for frontend-only
development.

```sh
cp .env.example .env
docker compose up --build
```

Open <http://localhost:8080>. Register an account, choose **Upload**, select a
supported video and watch the browser send the file parts to `/uploads/…`,
which the edge passes straight to the S3 gateway. The API only receives
metadata and ETags.

`APP_ORIGIN` must be the URL browsers use for the app (in production,
`https://videio.mauliksharma.org`): upload URLs are signed for that host.

## Checks

```sh
cd backend
uv sync --frozen
uv run ruff check
uv run ruff format --check
uv run mypy app
uv run pytest tests/unit

cd ../web
npm ci
npm test
npm run lint
npm run format:check
npm run build
```

Integration tests use the full Compose stack and real PostgreSQL, Redis and
SeaweedFS:

```sh
docker compose -f compose.yaml -f compose.ci.yaml up -d --build --wait
cd backend && uv run pytest tests/integration
```

## Architecture

The edge nginx container serves the React SPA and proxies `/api` to the
FastAPI service. FastAPI writes metadata to the PostgreSQL primary and reads
the authenticated user's video list from the replica while Redis provides
read-your-writes routing. SeaweedFS exposes S3-compatible `uploads` and
`media` buckets. The browser receives presigned multipart URLs and uploads
directly to SeaweedFS; the API never proxies the file bytes. Completing an
upload queues a `transcode_video` task on RabbitMQ. Worker containers run
ffmpeg, write HLS renditions and a thumbnail to the `media` bucket, and the
edge serves them from an nginx cache under `/media/`. Scale workers with
`docker compose up -d --scale worker=3`.

See [`docs/design.md`](docs/design.md) for design trade-offs and
[`docs/runbook.md`](docs/runbook.md) for deployment, rollback and Nginx Proxy
Manager setup.

## Future work

Private media authorization, admin takedown, cache-aside reads and load-test
results are future work. Comments, likes, subscriptions, search,
recommendations, email verification, password reset and live streaming remain
out of scope.
