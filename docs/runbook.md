# Runbook

Operational procedures for Videio in production. Production runs as a
Portainer stack on the VPS, behind Nginx Proxy Manager (NPM), at
<https://videio.mauliksharma.org>.

## How a deploy works

1. A push to `main` runs **CI** (`.github/workflows/ci.yml`): lint, tests, then
   builds and pushes `ghcr.io/maulik-sharma/videio-{api,web}` tagged
   `sha-<7 chars>` and `latest`.
2. When CI succeeds, **Deploy** (`.github/workflows/deploy.yml`) joins the
   tailnet as a temporary `tag:ci` node, then POSTs the Portainer stack
   webhook with `?IMAGE_TAG=sha-<7 chars>`. Portainer is not exposed to the
   internet and is reachable only over Tailscale. Portainer pulls those images
   and redeploys the stack.
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

### 4. Tailscale access for the deploy job

Portainer (`maulikpi`, `100.99.220.35:9443`) is reachable only inside the
tailnet. The deploy job joins as an ephemeral node tagged `tag:ci`, which the
policy allows to reach that one port and nothing else.

**Access policy** (admin console → Access controls). The policy's default
`acls` rule (`src: ["*"]`) also matches tagged nodes, which would give
`tag:ci` access to every device. Replace that rule with these entries, and
leave other sections such as `ssh` as they are:

```jsonc
"tagOwners": {
  "tag:ci": ["autogroup:admin"],
},
"hosts": {
  "portainer": "100.99.220.35",
},
"grants": [
  // Your own devices keep reaching everything, as before.
  {"src": ["autogroup:member"], "dst": ["*"], "ip": ["*"]},
  // CI runners reach only the Portainer API/webhook.
  {"src": ["tag:ci"], "dst": ["portainer"], "ip": ["tcp:9443"]},
],
```

If you tag any of your own devices later, add their tags to the first
grant's `src`. Otherwise they lose access the same way `tag:ci` is limited.

**Trust credential** (admin console → Settings → Trust credentials → add an
OpenID Connect credential):

- Issuer: GitHub (`https://token.actions.githubusercontent.com`)
- Subject: `repo:maulik-sharma/Videio:environment:production`. Only jobs
  running in the `production` environment of this repo can use it.
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
