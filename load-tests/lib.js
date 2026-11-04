// Shared helpers for the k6 scripts. See load-tests/README.md.
import http from 'k6/http';
import { check, fail } from 'k6';

export const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/$/, '');
export const CSRF = { 'X-Requested-With': 'videio' };

// Ids of public, ready videos from the first feed pages.
export function publicVideoIds(pages = 3) {
  const ids = [];
  let cursor = null;
  for (let page = 0; page < pages; page += 1) {
    const query = cursor ? `?limit=50&cursor=${encodeURIComponent(cursor)}` : '?limit=50';
    const response = http.get(`${BASE_URL}/api/feed${query}`);
    if (response.status !== 200) fail(`feed returned ${response.status}`);
    const body = response.json();
    body.items.forEach((item) => ids.push(item.public_id));
    cursor = body.next_cursor;
    if (!cursor) break;
  }
  if (ids.length === 0) fail('the feed is empty: upload a few public videos first');
  return ids;
}

export function xCache(response) {
  return response.headers['X-Cache'] || response.headers['x-cache'] || 'NONE';
}

export function expectOk(response, name) {
  return check(response, { [`${name} is 200`]: (r) => r.status === 200 });
}
