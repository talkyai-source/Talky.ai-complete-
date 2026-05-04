// k6 soak test — sustained load to surface memory leaks, pool exhaustion,
// slow leaks in worker queues. Run for 30+ minutes against staging, never
// production.
//
//   k6 run --duration 30m backend/tests/load/soak.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errors = new Rate('errors');

export const options = {
  scenarios: {
    sustained: {
      executor: 'constant-arrival-rate',
      rate: 50,                  // 50 RPS — tune to your target peak
      timeUnit: '1s',
      duration: '30m',
      preAllocatedVUs: 50,
      maxVUs: 200,
    },
  },
  thresholds: {
    http_req_failed:   ['rate<0.005'],
    http_req_duration: ['p(95)<800', 'p(99)<2000'],
    errors:            ['rate<0.005'],
  },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const TOKEN = __ENV.AUTH_TOKEN || '';
const HEADERS = TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {};

export default function () {
  const r = http.get(`${BASE}/api/v1/health`, { headers: HEADERS });
  const ok = check(r, { 'status 200': (x) => x.status === 200 });
  errors.add(!ok);
  sleep(0.1);
}
