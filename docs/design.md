# Design notes

Each system-design concept in Videio: what it is, why it is here, the trade-off
it makes, and how to see it working. Sections are added as each phase lands.

A note on scale: Videio serves a handful of users. A read replica, token-bucket
limits and refresh-token rotation are here to learn and demonstrate the
patterns, not because the load needs them. Each section says when a real system
would reach for the pattern.

## Read replica

**What.** One PostgreSQL primary takes every write. A second Postgres runs as a
hot standby, fed by streaming replication (`pg_basebackup` once, then WAL over a
physical replication slot). The API has two engines: `write` (primary) and
`read` (replica). Session lookups (`GET /api/me`) read from the replica. Later
phases add the feed and video pages.

**Why.** Reads usually outnumber writes by a wide margin. Sending them to a
replica takes load off the one machine that has to accept writes. The replica
can also be promoted if the primary is lost.

**Trade-off.** Replication is asynchronous, so the replica can be slightly
behind. A read may not see a write that just committed (see read-your-writes
below). There are also two databases to run and monitor, and the replication
slot makes the primary keep WAL until the replica has it. A dead replica would
fill the disk, so `max_slot_wal_keep_size=1GB` caps that and lets the slot
lapse instead.

**When for real.** When read load, not write load, is what limits the primary,
and caching cannot absorb it. At Videio's size one Postgres would do.

**How to see it.** `GET /api/admin/system` (admins) lists each replica with its
state (`streaming`) and lag in bytes and seconds. On the replica,
`SELECT pg_is_in_recovery()` returns `t` and writes fail as read-only.

## Read-your-writes

**What.** After a user writes, the API sets `rw:{userId}` in Redis for 5
seconds. While that key exists, the user's reads go to the primary. Everyone
else keeps reading from the replica. Login also always reads the primary, since
nobody is logged in yet to carry the flag.

**Why.** Without it, a user who registers and immediately loads their profile
can get "not found" from a replica that has not caught up yet. Their own change
appears to vanish.

**Trade-off.** It guarantees consistency only for the person who wrote. Others
may briefly see the old data, which is fine for a video site. Each read costs
one Redis `EXISTS`, and a burst of writes pushes that user's reads to the
primary. The 5 seconds is a guess well above normal lag (milliseconds). If the
replica ever lags longer, the guarantee lapses. A stricter version would store
the primary's WAL position at write time and read from the replica only once it
has replayed past it.

**How to see it.** `tests/integration/test_replication.py` pauses WAL replay on
the replica and registers a user. `/api/me` succeeds through the flag, and
returns 401 once the flag is deleted, because the replica has never seen that
user.

## Sessions: short JWTs, rotating refresh tokens

**What.** Login sets two `httpOnly`, `Secure`, `SameSite=Lax` cookies:

- an access token: a 15-minute HS256 JWT holding the user id and role;
- a refresh token: 256 random bits, sent only to `/api/auth/*`.

The refresh token is stored in Redis by its SHA-256 hash, as `rt:{hash}` → user
id, with a 7-day TTL. Every refresh consumes the old token (`GETDEL`), leaves a
tombstone `rt_used:{hash}`, and issues a new one. Presenting a token that has a
tombstone means a copy of an already-used token exists. The API then revokes
every session of that user.

**Why.** The API checks the JWT's signature without any shared session state,
so any API replica can serve any request. Rotation keeps a stolen refresh token
useful only until the next refresh, and reuse detection turns a theft into a
visible event that logs both parties out. Cookies rather than an
`Authorization` header mean the browser attaches identity to HLS segment
requests automatically, which edge authorization relies on in a later phase.

**Trade-off.** Access tokens cannot be revoked before they expire. The API
still loads the user on each authenticated request, so a disabled account is
refused immediately anyway. Reuse detection can also misfire: two browser tabs
refreshing at the same instant both present the same token. One wins and the
other trips detection, which logs the user out. The SPA reduces this by sharing
one in-flight refresh per tab. Hashing tokens means a Redis dump yields nothing
that can be replayed.

**How to see it.** `test_reusing_a_rotated_refresh_token_revokes_every_session`
replays a rotated token from a second client. The replay gets
`REFRESH_TOKEN_REUSED`, and both legitimate sessions are then logged out.

## CSRF protection without tokens

**What.** Every mutating `/api` request must carry `X-Requested-With: videio`;
otherwise the API answers 403 `CSRF_HEADER_REQUIRED`.

**Why.** A cross-site form post or image tag cannot set a custom header. A
cross-site `fetch` that sets one triggers a CORS preflight, which the API never
approves. Combined with `SameSite=Lax` cookies, this blocks CSRF without
issuing and checking per-session tokens.

**Trade-off.** It relies on browsers enforcing CORS, and on the API never
enabling CORS for other origins. Any future CORS change has to keep that in
mind.

## Rate limiting

**What.** A token bucket per key in Redis, updated atomically by a Lua script
that uses Redis' clock, so all API replicas share one view. Register allows 5
per minute per client IP. Login allows 5 per minute per email address,
case-insensitive. Over the limit, the API answers 429 with `Retry-After`.

**Why.** Login is limited per email because that is what password guessing
targets: one IP or many, each account gets 5 guesses a minute. A token bucket
refills smoothly (one token every 12 seconds) instead of resetting all at once
at a window boundary.

**Trade-off.** Per-email limiting lets anyone who knows an email lock its owner
out of logging in for about a minute at a time. That is acceptable here; a
larger system would combine per-email and per-IP buckets, or use a CAPTCHA,
before locking. The per-IP register limit needs the real client IP. Nginx Proxy
Manager adds it, the edge resolves it with `real_ip` from trusted private
ranges, and then overwrites `X-Forwarded-For` with that single address.
Appending would let a client prepend a fake address, which uvicorn would
believe.

**How to see it.** Six quick wrong logins for one email: the sixth returns 429
with `Retry-After`. `tests/integration/test_rate_limits.py` covers both limits.

## No sharding

All tables live in one database. A few thousand users and videos fit easily on
one small instance. Sharding would add a routing layer, cross-shard queries and
more containers for no gain. It would become worth considering when write
volume or table size outgrows a single primary, even after adding replicas and
caching.

## Direct-to-storage multipart uploads

**What.** `POST /api/videos` stores only video metadata in the primary database,
starts an S3 multipart upload in SeaweedFS, and returns one SigV4-presigned PUT
URL per 10 MiB part. The browser uploads up to three parts concurrently and
sends the returned ETags to `POST /api/videos/{publicId}/complete`. The API
never receives the video bytes. Completion verifies the final object size before
changing the row from `pending_upload` to `uploaded`.

**Why.** A 200 MB upload does not consume an API worker's memory, connection or
bandwidth. The API remains responsible for authorization, metadata and the
state transition while SeaweedFS handles the bytes.

**Trade-off.** Multipart state can be abandoned if a browser closes halfway
through. Phase 2 records the multipart id and has the later maintenance phase
own expiry cleanup; retries of the final request are idempotent after the row
is marked `uploaded`. Presigned URLs are deliberately short-lived and tied to
the exact public S3 host. Path-style addressing and a separate `s3.` host avoid
the signature changing between the URL returned by the API and the URL reached
by the browser.

**How to see it.** Log in, open **Upload**, select a supported video and watch
the browser Network panel: the JSON requests go to `/api`, while the large PUT
requests go directly to the S3 host. The **My videos** page shows the row as
`Pending upload` until the completion request changes it to `Uploaded`.

## Video metadata and public IDs

The `videos` table uses a numeric identity key for joins and an 11-character
random base62 `public_id` for URLs. The random public id makes unlisted links
hard to guess without forcing every internal query to use a string key. My
videos uses `(created_at, id)` keyset pagination; it never uses `OFFSET`, so
walking through older uploads does not make the database scan and discard every
previous page.
