# Runbook

Operational procedures for Videio in production. Production runs as a
Portainer stack on the VPS, behind Nginx Proxy Manager (NPM), at
<https://videio.mauliksharma.org>.

## How a deploy works

1. A push to `main` runs **CI** (`.github/workflows/ci.yml`): lint, tests, then
   builds and pushes `ghcr.io/maulik-sharma/videio-{api,web}` tagged
   `sha-<7 chars>` and `latest`, for `linux/amd64` and `linux/arm64` (the VPS
   is arm64; the arm64 variant is built under QEMU).
2. When CI succeeds, **Deploy** (`.github/workflows/deploy.yml`) joins the
   tailnet as a temporary `tag:ci` node, then POSTs the Portainer stack
   webhook with `?IMAGE_TAG=sha-<7 chars>`. Portainer is not exposed to the
   internet and is reachable only over Tailscale. Portainer pulls those images
   and redeploys the stack.
3. The `migrate` service runs `alembic upgrade head` against the primary and
   exits; the API containers start only after it succeeds.
4. The `smoke` job polls `https://videio.mauliksharma.org/readyz` until it
   returns 200 (primary, replica and Redis reachable) and reports
   `"version": "sha-<7 chars>"`. If that does not happen within about
   5 minutes, the workflow fails.

## Rollback

Actions → **Deploy** → **Run workflow** → enter a previous tag (e.g.
`sha-1a2b3c4`; the tags are listed under the repo's Packages). The same
webhook and smoke check run against that tag.

Migrations only run forward. Rolling back to an image older than the
database schema works only while the old code tolerates the newer schema
(e.g. an added nullable column). Check `backend/migrations/versions/` for
migrations added since the target tag before rolling back across one.

## Admin accounts

Registration always creates a `user`. To make an existing account an admin,
open a console in one of the stack's `api` containers (Portainer →
Containers → `videio-api-1` → Console, `/bin/sh`) and run:

```sh
python -m app.cli promote-admin someone@example.com
```

It takes effect on that user's next request. Locally:
`docker compose exec api python -m app.cli promote-admin someone@example.com`.

## Phase 1 rollout (one time)

Phase 1 adds the replica, Redis, the `migrate` job and two secrets. The
Portainer stack is pasted compose text, so it does not update itself:

1. Merge the Phase 1 PR. CI pushes the new images, and the automatic
   Deploy is **expected to fail**: Portainer redeploys the new images with
   the old stack definition, which has no Redis, replica or `JWT_SECRET`.
2. In Portainer, open the `videio` stack and add the environment variables
   `REPLICATION_PASSWORD` and `JWT_SECRET` (each `openssl rand -hex 32`).
3. Stop the stack, then delete its primary database volume (Volumes →
   `videio_core-primary-data`). It holds no data yet, and Postgres only runs
   the replication init script on an empty data directory.
4. Paste the new `compose.prod.yaml` into the stack editor and deploy.
5. Re-run the failed Deploy run (**Re-run failed jobs**). It triggers the
   webhook with the same tag, and the smoke check should now pass.
6. Register your account on the site and promote it (see Admin accounts).
   The System numbers are at `/api/admin/system`.

## Storage and queue rollout (one time)

This adds SeaweedFS (S3) and RabbitMQ to the stack. The app does not use them
yet (Phase 2 and 3 do), so the order of merging and updating does not matter,
and no existing volume is touched.

1. In Portainer, open the `videio` stack and add the environment variables:
   - `S3_ACCESS_KEY` (`openssl rand -hex 16`), `S3_SECRET_KEY` and
     `RABBITMQ_PASSWORD` (each `openssl rand -hex 32`);
   - `S3_PORT`, only if host port 8333 is already taken on the VPS;
   - `RABBITMQ_UI_BIND`, only to reach the management UI from the tailnet:
     set it to the VPS's Tailscale IP. It defaults to `127.0.0.1`.
2. Paste the new `compose.prod.yaml` into the stack editor and update the
   stack with **Re-pull image** on.
3. Check that `seaweedfs` and `rabbitmq` show as healthy.

Notes:

- The S3 port is published on `S3_BIND` (default all interfaces) because
  browsers will upload to it through NPM. Docker-published ports bypass
  `ufw`, so it is reachable from the internet, but every request must be
  signed with the S3 keys. Anonymous requests get 403.
- `RABBITMQ_PASSWORD` is applied only when RabbitMQ first initialises its
  volume. To change it later, run
  `rabbitmqctl change_password videio <new>` in the container, then update
  the variable.
- If you bind the UI to the Tailscale IP, Tailscale must be up before
  Docker starts the container, or the port fails to bind and the
  container does not start.

## One-time setup

### 1. DNS

An `A` record for `videio.mauliksharma.org` pointing at the VPS.

### 2. GHCR access for Portainer

GHCR packages are private by default. Do one of these:

- In Portainer: **Registries → Add registry → Custom registry**, URL
  `ghcr.io`, username `maulik-sharma`, password a GitHub PAT (classic) with
  only `read:packages`.
- Or make the `videio-api` and `videio-web` packages public in their package
  settings on GitHub.

The packages only exist after the first CI run on `main` has pushed them.

### 3. Portainer stack

1. **Stacks → Add stack → Web editor**, name `videio`.
2. Paste the contents of `compose.prod.yaml`.
3. Under **Environment variables**, add the variables from `.env.example`.
   Generate `POSTGRES_PASSWORD`, `REPLICATION_PASSWORD` and `JWT_SECRET` with
   `openssl rand -hex 32`, and set `EDGE_PORT` to a free host port.
4. Deploy the stack.
5. Open the stack's **Editor** tab, toggle **Create a stack webhook** and
   copy the URL. Stack webhooks are a Portainer Business Edition feature.

Changes to `compose.prod.yaml` are not picked up automatically: the webhook
only re-pulls images. Paste the new file into the stack editor when it
changes.

### 4. Tailscale access for the deploy job

Portainer (`maulikpi`, `100.99.220.35:9443`) is reachable only inside the
tailnet. The deploy job joins as an ephemeral node tagged `tag:ci`, which the
policy allows to reach that one port and nothing else.

**Access policy** (admin console → Access controls). The existing rule
`src: ["maulik.jaipur@gmail.com"] → *:*` covers only devices logged in as
that user. Tagged devices belong to the tag, not the user, so it does not
cover `tag:ci`. The policy adds these top-level sections (siblings of
`acls`, not inside it), plus one rule in `acls`:

```jsonc
"tagOwners": {
    "tag:ci": ["autogroup:admin"],
},
"hosts": {
    "portainer": "100.99.220.35",
},
"acls": [
    {"action": "accept", "src": ["maulik.jaipur@gmail.com"], "dst": ["*:*"]},
    // GitHub deploy job: only the Portainer webhook port.
    {"action": "accept", "src": ["tag:ci"], "proto": "tcp", "dst": ["portainer:9443"]},
],
"tests": [
    {
        "src":    "tag:ci",
        "accept": ["portainer:9443"],
        "deny":   ["portainer:22", "100.68.101.98:443", "100.125.113.127:22"],
    },
    {"src": "maulik.jaipur@gmail.com", "accept": ["portainer:9443", "100.68.101.98:443"]},
],
```

The `tests` run on every save, and Tailscale rejects any edit that would
widen what `tag:ci` can reach. Never add a `src: ["*"]` rule: `*` includes
tagged devices.

**Trust credential** (admin console → Settings → Trust credentials → add an
OpenID Connect credential):

- Issuer: GitHub (`https://token.actions.githubusercontent.com`)
- Subject: `repo:maulik-sharma@88702557/Videio@1443300543:environment:production`.
  Only jobs running in the `production` environment of this repo can use it.
  GitHub's `sub` includes the numeric owner and repo IDs, so the plain
  `repo:maulik-sharma/Videio:...` form never matches. The IDs never change on
  a rename, so a recreated repo with the same name can't use the credential.
  On a mismatch, the credential's page in the admin console shows the value
  it received.
- Custom claims: none (the subject already pins repo and environment).
- Scope: Auth Keys (write), tag `tag:ci`

It produces a **client ID** and an **audience**. These are not secret: GitHub
signs a short-lived token per job, and Tailscale only accepts tokens whose
subject matches.

### 5. GitHub environment

Repo **Settings → Environments → New environment** named `production`.
Optionally restrict deployments to the `main` branch.

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `PORTAINER_WEBHOOK_URL` | Stack webhook URL using the Tailscale IP, e.g. `https://100.99.220.35:9443/api/stacks/webhooks/<id>` |
| Variable | `PORTAINER_TAILNET_HOST` | `100.99.220.35` (pinged to confirm the tailnet path is up) |
| Variable | `PORTAINER_PUBKEY_PIN` | `sha256//sZJ+ZtM6gtwIi692kKlIVwta9hUHi5HSSVe6ju0JYBg=` |
| Variable | `TS_CLIENT_ID` | Client ID from the trust credential |
| Variable | `TS_AUDIENCE` | Audience from the trust credential |

**About the pin:** Portainer's self-signed certificate only names
`localhost`, so curl skips the normal check and instead requires the
server's public key to match this hash. The certificate expires in May 2031.
If Portainer ever regenerates it, deploys fail with curl exit code 90.
Recompute the pin from a tailnet machine:

```sh
echo | openssl s_client -connect 100.99.220.35:9443 2>/dev/null \
  | openssl x509 -pubkey -noout | openssl pkey -pubin -outform der \
  | openssl dgst -sha256 -binary | base64
# prefix the output with sha256//
```

### 6. Nginx Proxy Manager proxy host

| Field | Value |
| --- | --- |
| Domain | `videio.mauliksharma.org` |
| Scheme / Forward host / port | `http` / VPS IP / `EDGE_PORT` |
| SSL | Let's Encrypt, Force SSL, HTTP/2 |
| Advanced | `client_max_body_size 10m;` |

The edge trusts `X-Forwarded-For` only from private address ranges
(`10/8`, `172.16/12`, `192.168/16`), which covers NPM reaching it through
Docker. If the logs show NPM's address instead of real client IPs, check
which address NPM connects from and add it to `set_real_ip_from` in
`infra/nginx/nginx.conf`.

**Firewall:** ports published by Docker bypass `ufw`, so with
`EDGE_BIND=0.0.0.0` the edge port is reachable from the internet over plain
HTTP, skipping NPM. To keep it private, either set `EDGE_BIND` to an address
only NPM uses (e.g. the Docker bridge gateway `172.17.0.1`, then forward NPM
to that address), or block the port in the `DOCKER-USER` iptables chain.
