// k6 smoke test — minimal traffic, verifies the API is alive end-to-end.
// Run:  k6 run backend/tests/load/smoke.js
//
//   BASE_URL   target backend (default http://localhost:8000)
//   AUTH_TOKEN bearer token; required for authenticated endpoints
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds: {
    http_req_failed:   ['rate<0.01'],          // <1% errors
    http_req_duration: ['p(95)<500'],          // 95% under 500ms
  },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const TOKEN = __ENV.AUTH_TOKEN || '';
const HEADERS = TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {};

export default function () {
  const r1 = http.get(`${BASE}/health`);
  check(r1, { 'liveness 200': (r) => r.status === 200 });

  const r2 = http.get(`${BASE}/api/v1/health`, { headers: HEADERS });
  check(r2, {
    'readiness 200':           (r) => r.status === 200,
    'has X-Request-ID header': (r) => !!r.headers['X-Request-Id'],
  });

  sleep(1);
}
