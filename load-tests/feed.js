// Feed: the cached first page, and keyset pages deeper down that are not cached.
//   k6 run load-tests/feed.js
//   k6 run -e VUS=8 -e DURATION=1m load-tests/feed.js
import http from 'k6/http';
import { sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import { BASE_URL, expectOk, xCache } from './lib.js';

const cacheHits = new Rate('feed_cache_hits');
const firstPage = new Trend('feed_first_page_ms', true);
const deepPage = new Trend('feed_deep_page_ms', true);

export const options = {
  vus: Number(__ENV.VUS || 8),
  duration: __ENV.DURATION || '1m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    feed_first_page_ms: ['p(95)<150'],
    feed_deep_page_ms: ['p(95)<300'],
  },
};

export default function () {
  const first = http.get(`${BASE_URL}/api/feed?limit=20`, { tags: { page: 'first' } });
  expectOk(first, 'first page');
  cacheHits.add(xCache(first) === 'HIT');
  firstPage.add(first.timings.duration);

  // Follow the cursor a couple of pages down, as infinite scroll would.
  let cursor = first.status === 200 ? first.json('next_cursor') : null;
  for (let page = 0; cursor && page < 2; page += 1) {
    const next = http.get(`${BASE_URL}/api/feed?limit=20&cursor=${encodeURIComponent(cursor)}`, {
      tags: { page: 'deep' },
    });
    expectOk(next, 'deeper page');
    deepPage.add(next.timings.duration);
    cursor = next.status === 200 ? next.json('next_cursor') : null;
  }
  // The edge allows 20 requests a second per IP; stay under it (see README).
  sleep(Number(__ENV.SLEEP || 0.5));
}
