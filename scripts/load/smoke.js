import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

export const errorRate = new Rate('errors');

const targetUrl = __ENV.TARGET_URL || 'http://127.0.0.1:8000';

export const options = {
  vus: 5,
  duration: '30s',
  thresholds: {
    http_req_duration: ['p(95)<500'],
    errors: ['rate<0.01'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  // 1. Health liveness probe
  const liveRes = http.get(`${targetUrl}/health/live`);
  const liveOk = check(liveRes, {
    'liveness status is 200': (r) => r.status === 200,
  });
  errorRate.add(!liveOk);

  // 2. Health readiness probe
  const readyRes = http.get(`${targetUrl}/health/ready`);
  const readyOk = check(readyRes, {
    'readiness status is 200 or 503': (r) => r.status === 200 || r.status === 503,
  });
  errorRate.add(!readyOk);

  sleep(1);
}
