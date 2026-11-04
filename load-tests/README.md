# Load tests

Three [k6](https://k6.io) scripts, one per hot path. They run against the edge
(`BASE_URL`, default `http://localhost:8080`), so they exercise nginx, the API,
Redis and Postgres together.

| Script | What it does | What to watch |
| --- | --- | --- |
| `feed.js` | First feed page, then two pages down by cursor | `feed_cache_hits`, `feed_first_page_ms` vs `feed_deep_page_ms` |
| `video.js` | Video metadata, master and variant playlists, one segment | `video_api_cache_hits`, `video_edge_cache_hits`, `video_segment_ms` |
| `upload.js` | Create, PUT the bytes to storage, complete | `upload_total_ms`, `upload_rate_limited` |

Without a local k6, use the container (the scripts are mounted read-only):

```sh
docker run --rm --network host -v "$PWD/load-tests:/scripts:ro" grafana/k6 run /scripts/feed.js
docker run --rm --network host -v "$PWD/load-tests:/scripts:ro" grafana/k6 run /scripts/video.js

ffmpeg -f lavfi -i testsrc=duration=3:size=640x360 -pix_fmt yuv420p /tmp/clip.mp4
docker run --rm --network host -v "$PWD/load-tests:/scripts:ro" -v /tmp/clip.mp4:/clip.mp4:ro \
  grafana/k6 run -e CLIP=/clip.mp4 /scripts/upload.js
```

`feed.js` and `video.js` need a few public, ready videos. `upload.js`
registers three throwaway accounts and uploads unlisted copies of the clip.

**Knobs:** `-e VUS=…`, `-e DURATION=…`, `-e SLEEP=…` (seconds between
iterations), and for uploads `-e ACCOUNTS=…` and `-e ITERATIONS=…`.

**Limits are part of the system under test.** k6 sends from one IP, and the
edge allows 20 API requests a second per IP (burst 50), so the defaults stay
just under it. Push harder and nginx answers 429, which is the per-IP limit
working, not the API failing. Each account may start 10 uploads an hour, so
`upload.js` expects some 429s on create once an account runs out; they are
counted in `upload_rate_limited`, not as failures.

## Before and after caching

To compare, run `feed.js` once as is and once with the cache defeated, e.g. by
flushing Redis in a loop (`watch -n 0.5 docker compose exec redis redis-cli DEL feed:head`).
On the local stack (one API container, a laptop) the first page took a median
of about 8 ms with a 99% hit ratio. Edge-cached segments came back in about
2 ms, with 96% of segment requests served from the nginx cache.
