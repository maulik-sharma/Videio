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

Transcoding, playback, feed pagination, private-media authorization, caching
and counters are planned for later phases.

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
directly to SeaweedFS; the API never proxies the file bytes. RabbitMQ is
provisioned for the Phase 3 transcoding path, when worker containers will
consume its jobs.

See [`docs/design.md`](docs/design.md) for design trade-offs and
[`docs/runbook.md`](docs/runbook.md) for deployment, rollback and Nginx Proxy
Manager setup.

## Future work

Transcoding to HLS, public feed and playback, private media authorization,
admin takedown, cache-aside reads, write-behind view counters and load-test
results are future work. Comments, likes, subscriptions, search,
recommendations, email verification, password reset and live streaming remain
out of scope.
