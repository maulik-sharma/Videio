# Runbook

Operational procedures for Videio in production. Production runs as a
Portainer stack on the VPS, behind Nginx Proxy Manager (NPM), at
<https://videio.mauliksharma.org>.

## How a deploy works

1. A push to `main` runs **CI** (`.github/workflows/ci.yml`): lint, tests, then
   builds and pushes `ghcr.io/maulik-sharma/videio-{api,web}` tagged
   `sha-<7 chars>` and `latest`.
2. When CI succeeds, **Deploy** (`.github/workflows/deploy.yml`) POSTs the
   Portainer stack webhook with `?IMAGE_TAG=sha-<7 chars>`. Portainer pulls
   those images and redeploys the stack.
3. The `smoke` job polls `https://videio.mauliksharma.org/healthz` until it
   reports `"version": "sha-<7 chars>"`. If that does not happen within about
   5 minutes, the workflow fails.

## Rollback

Actions → **Deploy** → **Run workflow** → enter a previous tag (e.g.
`sha-1a2b3c4`; the tags are listed under the repo's Packages). The same
webhook and smoke check run against that tag.

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
   Use a strong `POSTGRES_PASSWORD`, and set `EDGE_PORT` to a free host port.
4. Deploy the stack.
5. Open the stack's **Editor** tab, toggle **Create a stack webhook** and
   copy the URL. Stack webhooks are a Portainer Business Edition feature.

Changes to `compose.prod.yaml` are not picked up automatically: the webhook
only re-pulls images. Paste the new file into the stack editor when it
changes.

### 4. GitHub environment

Repo **Settings → Environments → New environment** named `production`, with
the secret `PORTAINER_WEBHOOK_URL` set to the webhook URL from the previous
step. Optionally restrict deployments to the `main` branch.

### 5. Nginx Proxy Manager proxy host

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
