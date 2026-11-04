// Video page: metadata (Redis cache-aside), then the HLS master playlist,
// one variant and one segment through the edge (nginx proxy_cache, with an
// auth_request check on every request).
//   k6 run load-tests/video.js
import http from 'k6/http';
import { sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import { BASE_URL, expectOk, publicVideoIds, xCache } from './lib.js';

const apiCacheHits = new Rate('video_api_cache_hits');
const edgeCacheHits = new Rate('video_edge_cache_hits');
const metadata = new Trend('video_metadata_ms', true);
const segment = new Trend('video_segment_ms', true);

export const options = {
  vus: Number(__ENV.VUS || 5),
  duration: __ENV.DURATION || '1m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    video_metadata_ms: ['p(95)<150'],
    video_segment_ms: ['p(95)<300'],
  },
};

export function setup() {
  return { ids: publicVideoIds() };
}

export default function ({ ids }) {
  const id = ids[Math.floor(Math.random() * ids.length)];
  const video = http.get(`${BASE_URL}/api/videos/${id}`, { tags: { name: 'video' } });
  if (!expectOk(video, 'metadata')) return;
  apiCacheHits.add(xCache(video) === 'HIT');
  metadata.add(video.timings.duration);

  const playback = video.json('playback_url');
  const base = playback.slice(0, playback.lastIndexOf('/'));
  const master = http.get(`${BASE_URL}${playback}`, { tags: { name: 'master.m3u8' } });
  expectOk(master, 'master playlist');
  const variant = http.get(`${BASE_URL}${base}/360p/index.m3u8`, { tags: { name: 'index.m3u8' } });
  if (!expectOk(variant, 'variant playlist')) return;
  const name = variant.body.split('\n').find((line) => line.endsWith('.ts'));
  const piece = http.get(`${BASE_URL}${base}/360p/${name}`, { tags: { name: 'segment' } });
  expectOk(piece, 'segment');
  edgeCacheHits.add(piece.headers['X-Edge-Cache'] === 'HIT');
  segment.add(piece.timings.duration);

  sleep(Number(__ENV.SLEEP || 1));
}
