// Upload: create the video, PUT the bytes straight to storage, complete it.
// Each account may start 10 uploads an hour, so this also shows the per-user
// limit: once an account is out of tokens, creates return 429 with Retry-After.
//   ffmpeg -f lavfi -i testsrc=duration=3:size=640x360 -pix_fmt yuv420p /tmp/clip.mp4
//   k6 run -e CLIP=/tmp/clip.mp4 load-tests/upload.js
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { BASE_URL, CSRF } from './lib.js';

const clip = open(__ENV.CLIP || fail('set CLIP to a small video file'), 'b');
const ACCOUNTS = Number(__ENV.ACCOUNTS || 3); // registration allows 5 a minute per IP
const PASSWORD = 'load-test-password';

const rateLimited = new Counter('upload_rate_limited');
const uploadTotal = new Trend('upload_total_ms', true);

export const options = {
  vus: Number(__ENV.VUS || 3),
  iterations: Number(__ENV.ITERATIONS || 36),
  thresholds: {
    // 429 is an expected answer here, not a failure.
    checks: ['rate>0.99'],
    upload_total_ms: ['p(95)<5000'],
  },
};

export function setup() {
  const run = Date.now();
  const sessions = [];
  for (let index = 0; index < ACCOUNTS; index += 1) {
    const email = `k6-${run}-${index}@example.com`;
    const register = http.post(
      `${BASE_URL}/api/auth/register`,
      JSON.stringify({ email, password: PASSWORD, display_name: `k6 ${index}` }),
      { headers: { ...CSRF, 'Content-Type': 'application/json' } },
    );
    if (register.status !== 201) fail(`register returned ${register.status}: ${register.body}`);
    const login = http.post(
      `${BASE_URL}/api/auth/login`,
      JSON.stringify({ email, password: PASSWORD }),
      { headers: { ...CSRF, 'Content-Type': 'application/json' } },
    );
    if (login.status !== 200) fail(`login returned ${login.status}`);
    sessions.push(login.cookies.videio_at[0].value);
  }
  return { sessions };
}

export default function ({ sessions }) {
  const token = sessions[(__VU + __ITER) % sessions.length];
  const headers = { ...CSRF, 'Content-Type': 'application/json', Cookie: `videio_at=${token}` };
  const started = Date.now();

  const created = http.post(
    `${BASE_URL}/api/videos`,
    JSON.stringify({
      title: `k6 upload ${__VU}-${__ITER}`,
      visibility: 'unlisted',
      size_bytes: clip.byteLength,
      content_type: 'video/mp4',
    }),
    { headers, tags: { name: 'create' } },
  );
  if (created.status === 429) {
    check(created, { 'rate limited with Retry-After': (r) => Boolean(r.headers['Retry-After']) });
    rateLimited.add(1);
    sleep(1);
    return;
  }
  if (!check(created, { 'create is 201': (r) => r.status === 201 })) return;

  const upload = created.json();
  const etags = upload.parts.map((part) => {
    const start = (part.part_number - 1) * upload.part_size_bytes;
    const bytes = clip.slice(start, start + upload.part_size_bytes);
    const put = http.put(part.url, bytes, { tags: { name: 'part' } });
    check(put, { 'part stored': (r) => r.status === 200 });
    return { part_number: part.part_number, etag: put.headers.Etag || put.headers.ETag };
  });

  const completed = http.post(
    `${BASE_URL}/api/videos/${upload.public_id}/complete`,
    JSON.stringify({ parts: etags }),
    { headers, tags: { name: 'complete' } },
  );
  check(completed, { 'complete is 202': (r) => r.status === 202 });
  uploadTotal.add(Date.now() - started);
  sleep(1);
}
