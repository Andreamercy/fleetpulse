// API load + soak.  k6 run -e BASE=http://localhost:8000 loadtest/k6_api.js            (smoke/load)
//                   k6 run -e BASE=... -e SOAK=1 loadtest/k6_api.js                    (2 h soak)
import http from 'k6/http'; import { check, sleep } from 'k6';
const soak = __ENV.SOAK === '1';
export const options = {
  scenarios: { api: { executor: 'ramping-arrival-rate', startRate: 50, timeUnit: '1s', preAllocatedVUs: 200, maxVUs: 1000,
    stages: soak ? [{ target: 500, duration: '5m' }, { target: 500, duration: '2h' }] : [{ target: 500, duration: '1m' }, { target: 1500, duration: '2m' }, { target: 500, duration: '1m' }] } },
  thresholds: { http_req_duration: ['p(95)<200', 'p(99)<500'], http_req_failed: ['rate<0.001'] },   // the case-study NFR
};
export function setup() { return http.get(`${__ENV.BASE}/dev/token?role=fleet_manager&tenant=t00`).json().token; }
export default function (token) {
  const h = { headers: { Authorization: `Bearer ${token}` } };
  const r = http.get(`${__ENV.BASE}/v1/vehicles?limit=25`, h);
  check(r, { 'vehicles 200': (x) => x.status === 200 });
  if (Math.random() < 0.3) check(http.get(`${__ENV.BASE}/v1/alerts?limit=25&min_severity=4`, h), { 'alerts 200': (x) => x.status === 200 });
  sleep(0.1);
}
