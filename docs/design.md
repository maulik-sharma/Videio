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

**Uploads** are limited per account the same way: 10 an hour
(`upload:user:{id}`). The bucket is per account, not per API replica, because
it lives in Redis, so running two API containers does not double the limit.

**At the edge**, nginx `limit_req` caps each IP at 20 API requests a second
(burst 50) and 2 a second on `/api/auth/` (burst 10). Over the limit it answers
429 itself, in the API's error shape with `Retry-After: 1`, before a request
reaches Python. The Redis buckets are precise and per account; the nginx limit
is coarse and per IP, and its job is to absorb floods cheaply. Media requests
are not limited: a player legitimately fetches many segments a second.

**How to see it.** Six quick wrong logins for one email: the sixth returns 429
with `Retry-After`. `tests/integration/test_rate_limits.py` covers both limits,
and `test_caching.py` the upload limit. For the edge limit, fire 100 parallel
requests at `/api/feed`: some come back 429.

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
the exact public host. SigV4 signs the `Host` header, so the URLs are signed
for the app's own origin (`APP_ORIGIN`) and the edge proxies `PUT /uploads/…`
(path-style: the bucket is the first path segment) to SeaweedFS, forwarding the
browser's `Host` unchanged. Keeping uploads on the app's origin means no second
DNS name or certificate (a nested `s3.videio.` name is not covered by
Cloudflare's free wildcard certificate) and no CORS. The cost is that upload
bytes pass through the edge nginx, which streams them without buffering.

**How to see it.** Log in, open **Upload**, select a supported video and watch
the browser Network panel: the JSON requests go to `/api`, while the large PUT
requests go to `/uploads/…`, which the edge hands to SeaweedFS. The **My
videos** page shows the row as `Pending upload` until the completion request
changes it to `Uploaded`.

## Authorization: one policy function

**What.** `app/core/policy.py` has one pure function, `can(actor, action, video)`.
Routes never check ownership or visibility themselves; they ask the policy.
Its rules, tested cell by cell in `tests/unit/test_policy.py`:

| Action | Anonymous | Logged in | Owner | Admin |
| --- | --- | --- | --- | --- |
| View public or unlisted | yes | yes | yes | yes |
| View private | no | no | yes | yes |
| View removed | no | no | no, sees a notice | yes |
| Edit title, description, visibility | no | no | yes, unless removed | no |
| Delete | no | no | yes | no |
| Take down, restore | no | no | no | yes, with a reason |
| Disable an account | no | no | no | yes, with a reason |

A video that is not `ready` yet is visible only to its owner and admins.

**Why.** Rules repeated in several handlers drift apart. A pure function with
no database access is cheap enough to call on every media request, and its
matrix test documents the rules better than prose.

**Trade-off.** The routes still choose which error to show: someone who may
not see a video gets 404, so private ids do not leak; someone who may see it but
not change it gets 403; the owner of a removed video gets 410 with the reason.
The anonymous-friendly reads (`GET /api/videos/{id}`, `/internal/authz`) take
the role from the signed access token instead of the database, so a role change
or a disable reaches them within one token lifetime (15 minutes). Every route
that needs a logged-in user re-reads the account and refuses a disabled one.

## Takedowns and the audit log

**What.** Admins take a video down or restore it, and disable accounts, through
`/api/admin/...`, always with a reason. Each action inserts an `audit_log` row
(who, what, which target, why) in the same transaction as the change. A
takedown sets `removed_at`, `removed_by` and `removal_reason`; the video leaves
the feed, its page and its media, and the owner sees the reason in My videos
and on the video's URL. Disabling an account also revokes all its refresh
tokens. Owners delete their own videos for good: the original, every generated
file and the row.

**Why.** A takedown is reversible and keeps the evidence, which a delete does
not, so admins take down and owners delete. Writing the audit row in the same
transaction means there is never an action without a record.

**Trade-off.** The transcode keeps going if a video is taken down mid-job, so a
restore needs no re-run. A delete removes the objects before the row: if
storage is down the delete fails and can be retried, rather than leaving files
nothing points at. A transcode that finishes after the delete finds no row and
removes what it uploaded.

**How to see it.** Open **Admin**, take a video down, and open its link as the
owner (notice with the reason) and logged out (not found). The **Recent admin
actions** list shows the audit rows. `tests/integration/test_video_rbac.py`
covers each step.

## Authorization at the edge

**What.** Every request under `/media/` (playlists, segments, thumbnails) first
goes through nginx `auth_request` to `/internal/authz?video={publicId}` on the
API, carrying the browser's cookies. The API answers 204 or 403 from the cached
video record and the access token, with no database query. The location is
`internal`, so browsers cannot call it directly.

**Why.** Session cookies, not `Authorization` headers, were chosen so that
hls.js segment requests carry identity automatically. That makes it possible to
check private videos per segment while still letting nginx stream the bytes
from SeaweedFS.

**Trade-off.** Every segment costs one small API call, even when the segment
itself is in the edge cache; the call is a Redis read, so it is cheap. The
check runs before the cache, so making a video private or taking it down takes
effect on the next segment, even for copies already cached. Media responses
leave the edge as `Cache-Control: private`: a shared cache in front of NPM
(Cloudflare) must not keep copies, or it would serve them without asking.

**How to see it.** Make a ready video private. As the owner it still plays; a
logged-out browser gets 403 for `master.m3u8` even with the exact URL. The e2e
smoke test checks this through the real edge.

## Edge caching

**What.** nginx `proxy_cache` keeps public and unlisted HLS objects and
thumbnails for 7 days (`X-Edge-Cache: HIT` on the second load). The authz
response says whether a video may be cached (`X-Media-Cacheable`); private
videos bypass the cache in both directions.

**Why.** Generated objects never change (a retry overwrites the same keys with
the same content), so they can be cached for a long time, and most requests
for popular videos never reach storage.

**Trade-off.** A deleted video's segments may sit in the cache until they go
unused for 7 days, but the authz check refuses them, so they are never served.
The cache is per edge container; several edges would each warm their own.

**How to see it.** Load a public video's thumbnail twice and compare
`X-Edge-Cache`: `MISS`, then `HIT`. A private video's always shows `BYPASS`.

## Cache-aside for videos and the feed

**What.** `GET /api/videos/{id}` reads `video:{publicId}` from Redis (5
minutes); `GET /api/feed` without a cursor reads `feed:head`, the newest 51
public videos (30 seconds), enough for any page size. On a miss the API reads
the database (the replica) and fills the key with `SET NX`. Responses carry
`X-Cache: HIT` or `MISS`; deeper feed pages say `BYPASS` and always use the
keyset query. The cached record holds what the policy needs (owner, visibility,
status, removal), so access is still checked per caller on a hit.

**Invalidation.** Every write path (upload complete, owner edit and delete,
takedown and restore, each worker status change) calls `publish_video_change`
after committing. It reloads the row *from the primary* and overwrites the key,
and rebuilds the feed head when the change could affect it. The maintenance
tasks (view flush, reaper, cleanup) delete the keys instead.

**Why.** A plain delete-on-write has a race that the replica makes worse: right
after the delete, a reader misses, reads the replica before it has replayed the
write, and puts the old row back for 5 minutes. Overwriting with the primary's
row, while readers only ever fill with `SET NX`, closes that window: once the
fresh value is in Redis, a stale reader's fill fails.
`test_writes_replace_cached_data_even_while_the_replica_lags` pauses the
replica to prove it.

**Trade-off.** A write costs one or two extra primary queries. View counts in a
cached record can be up to a flush behind, so the API adds the pending Redis
count on every read (see below). At this size Postgres alone would be fast
enough; the cache is here to show the pattern and to keep reads flat under
load (see the k6 numbers in `load-tests/README.md`).

**How to see it.** `curl -sI localhost:8080/api/feed | grep X-Cache` twice.
Change a video's visibility, and the feed reflects it on the next request. The
admin System panel shows hit ratios for both caches.

## Write-behind view counters

**What.** `POST /api/videos/{id}/view` counts at most one view per user (or IP)
per video per hour (`SET NX EX`), then `INCR views:{id}` and `SADD views:dirty`.
A scheduled task adds the counts to Postgres every minute in one `UPDATE`.
Reads return the Postgres total plus the pending Redis count, so a view shows up
at once.

**Why.** A view is the most frequent write in the system. Turning it into a
Redis increment keeps Postgres write load flat no matter how popular a video
is.

**Trade-off.** If Redis is lost, up to a minute of views is lost with it. The
flush removes ids from `views:dirty` before reading their counts, so a view
arriving mid-flush is counted in the next run, never twice. Views of removed
videos are not counted.

**How to see it.** Play a video: the count goes up at once, while the row in
Postgres catches up within a minute. The System panel shows how many videos have
views waiting to flush.

## Video metadata and public IDs

The `videos` table uses a numeric identity key for joins and an 11-character
random base62 `public_id` for URLs. The random public id makes unlisted links
hard to guess without forcing every internal query to use a string key. My
videos uses `(created_at, id)` keyset pagination; it never uses `OFFSET`, so
walking through older uploads does not make the database scan and discard every
previous page.
The public feed (`GET /api/feed`) uses the same keyset pattern over public,
ready, not-removed videos, reads from the replica, and is served by the partial
index `(created_at desc) where visibility='public' and status='ready' and
removed_at is null`.

## Async transcoding with a queue

**What.** `POST /api/videos/{id}/complete` publishes `transcode_video(video_id)`
to RabbitMQ (taskiq with `taskiq-aio-pika`) and returns 202. Worker containers
consume the `videio.tasks` quorum queue with `qos=1` and acknowledge a message
only after the task has run (`--ack-type when_executed`). The worker probes the
original with `ffprobe`, encodes an HLS ladder with 6-second segments, writes
a master playlist and a thumbnail, and uploads them to the `media` bucket under
`videos/{publicId}/`. The edge serves them at `/media/{publicId}/…` through an
nginx cache.

**Why.** Transcoding takes seconds to minutes of CPU. Doing it inside an API
request would tie up a worker for the whole time and fail the upload whenever
the request timed out. A queue lets the API answer at once, keeps the job if a
worker dies (an unacknowledged message returns to the queue), and lets workers
scale separately from the API.

**Trade-off.** The video is not playable the moment the upload finishes; the
page shows "Processing" and polls every 3 seconds. There is one more service to
run, and a message published just as RabbitMQ goes away can be lost. If the
publish fails the API answers 503 and the row stays `uploaded`, so the
browser's retry of `/complete` publishes again; the reaper (below) covers a
worker dying mid-job.

**How to see it.** Open the RabbitMQ UI (`http://localhost:15672` in dev) during
an upload: `videio.tasks` shows the message ready, then unacked while a worker
holds it.

## The rendition ladder

**What.** Every standard rung up to the source's resolution, plus the source's
own resolution when it is not a standard rung. Rungs are named by the short
side, so a portrait phone video is treated like its landscape twin:

| Source | Rungs |
| --- | --- |
| 1080p | 1080p, 720p, 480p, 360p, 240p, 144p |
| 900p (not standard) | 900p, 720p, 480p, 360p, 240p, 144p |
| 4K or larger | 2160p, 1440p, 1080p, 720p, 480p, 360p, 240p, 144p |
| below 144p | the original only |

Nothing is upscaled, and 2160p is the ceiling. Bitrates come from a table
(150 kbps at 144p up to 14 Mbps at 2160p) and are interpolated for an
original between two rungs. Rotation metadata from phones is applied, so a
video recorded upright stays upright.

One ffmpeg run decodes the source once and `split`s it into one scaler and
encoder per rung. Every rung forces a keyframe every 6 s, so segments are clean
6-second cuts that line up across rungs and the player can switch quality at
any segment boundary.

**Why.** Small rungs keep playback going on a poor connection, and the top rung
keeps the quality the uploader had. Decoding once instead of once per rung
saves most of the work for a high-resolution source.

**Trade-off.** More rungs cost more CPU, storage and memory: a 4K source
encodes eight rungs, about 7.6 times the pixels of a single 1080p encode.
ffmpeg would size its thread pools from the host's CPUs (16 on the dev
machine), ignoring the container's limit, so the worker pins 2 decode threads
and 1 thread per encoder; the rungs already run in parallel. With 1.5 CPUs a
4K test clip went from 5.2 GB and 86 s with ffmpeg's defaults to 1.5 GB and
42 s with these, which is why the production worker gets 3 GB.

**Time limits.** The encode is stopped after (video length) x (pixels across
all rungs, counted in 1080p frames) x 2 s, and never before 10 minutes. With
1.5 CPUs a 1080p ladder ran at about 1.2 times the video's length and a 4K
ladder at about 4.4 times, so this leaves about three times that as headroom:
a 10-minute video may take 36 minutes at 1080p and 2.5 hours at 4K. A timeout
fails the video ("The video took too long to process.") instead of retrying,
because the same file on the same worker would only time out again.

## Horizontal worker scaling

`docker compose up -d --scale worker=3`. Each worker runs one task at a time
(`--workers 1`, prefetch 1), so RabbitMQ hands three uploads to three
different workers and they transcode in parallel. In production each worker is
capped at 1.5 CPUs and 3 GB. At Videio's size one worker is enough; a real
system would scale workers on queue depth.

## Retries with backoff, and the dead-letter queue

**What.** The task tells apart two kinds of failure. A *permanent* one (no video
stream, ffprobe cannot read the file, longer than 10 minutes, the encode ran
past its time limit) marks the video `failed` with a short reason and is not
retried. A *temporary* one (storage or database unavailable, ffmpeg killed by a
signal such as the out-of-memory killer) is retried by
taskiq's `SmartRetryMiddleware` after about 10 s, 60 s and then 5 min (plus up
to 10% jitter), using RabbitMQ's delay queue (`videio.tasks.delay`, a
per-message TTL that dead-letters back into `videio.tasks`). When the last of
the four attempts fails, the video is marked `failed` and the message is
published to `transcode.dlq`. Permanent failures go to the DLQ too.

taskiq acknowledges a failed task once its result is handled, so RabbitMQ never
dead-letters it by itself. A small middleware (`FinalFailureDeadLetterMiddleware`)
publishes the original message to the DLQ on the final failure, using the same
rule the retry middleware uses to decide whether another attempt will follow.

**Why.** Most transient failures fix themselves within seconds or minutes, so a
few spaced-out retries turn them into a short delay instead of a failed upload.
Retrying bad input would only waste CPU. The DLQ keeps every message that gave
up, with the error in its headers, so it can be inspected and replayed instead
of disappearing into a log.

**Trade-off.** Each attempt records a row in `transcode_attempts`, and ffmpeg's
error output is kept there, while the uploader only sees a short reason. A
video whose only problem is a long outage waits about six minutes before it is
marked failed.

**How to see it.** Upload a file that is not a video (`head -c 1000000
/dev/urandom > junk.mp4`). It ends `Failed: The file could not be read as a
video.`, and `transcode.dlq` in the RabbitMQ UI gains a message.

## Idempotent consumers

A message can be delivered more than once (a worker dies after finishing but
before acknowledging, the reaper re-kicks, or `/complete` is retried). The task
is safe to run twice:

- it only works on videos that are `uploaded`, or `processing` because a
  worker died mid-job. Ready, failed, removed or not-yet-uploaded videos are
  left alone;
- it holds a Redis lock `transcode:lock:{id}` for the whole job. The lock
  lives 3 minutes and the worker extends it every minute, so a live job keeps
  it however long it runs, while a dead worker's lock lapses within 3 minutes.
  A copy of the job that finds the lock taken does not drop the work: it puts
  itself back on the queue for a minute later. If the holder finished, that
  re-check sees the video is done and stops; if the holder died, it takes over
  as soon as the lock lapses. Re-checks do not use up the retry budget;
- outputs go to deterministic keys, so a re-run overwrites instead of
  duplicating. The master playlist is uploaded last, so it never points at a
  rendition that is missing.

## Graceful shutdown

On SIGTERM `taskiq worker` stops taking new messages and waits up to 300 s for
the running task (`--wait-tasks-timeout 300`); compose gives the container
310 s (`stop_grace_period`) before killing it. A redeploy during a transcode
therefore finishes a short job. A long one (a 4K video can take over an hour)
is killed after the 300 s; the message was never acknowledged, so RabbitMQ
redelivers it, and it is picked up again within about 3 minutes, once the dead
worker's lock lapses. It then starts over from the beginning: ffmpeg cannot
resume a half-written ladder. Waiting longer on shutdown would hold every
redeploy up for as long as the longest transcode.

## Scheduled tasks

One `scheduler` container (`taskiq scheduler`) only puts periodic tasks on the
queue; workers run them. It must run exactly once, or every task runs twice.
Each task also takes a short Redis lock, so overlapping runs do nothing.

- **Every minute:** flush view counts from Redis (`views:{id}`, `views:dirty`)
  into Postgres in one `UPDATE`.
- **Every 5 minutes, the reaper:** a video `processing` for over 20 minutes
  whose transcode lock has expired was abandoned by a worker that died. It goes
  back to `uploaded` and is re-kicked. Checking the lock means a long but live
  transcode is never started twice. After four recorded attempts the video is
  marked failed instead, so a file that crashes workers is not retried forever.
- **Every hour:** delete rows still `pending_upload` after 24 hours and abort
  their multipart uploads.
