import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  scenarios: {
    live_user_reads_and_cockpit: {
      executor: "ramping-vus",
      startVUs: 10,
      stages: [
        { duration: "1m", target: 60 },
        { duration: "3m", target: 120 },
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.005"],
    http_req_duration: ["p(95)<120", "p(99)<250"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const PAT_TOKEN = __ENV.LP_PAT_TOKEN || "";

export default function () {
  const headers = PAT_TOKEN
    ? { Authorization: `Bearer ${PAT_TOKEN}` }
    : {};

  const liveRes = http.get(`${BASE_URL}/health/live`);
  check(liveRes, { "live 200": (r) => r.status === 200 });

  if (PAT_TOKEN) {
    const pnl = http.get(
      `${BASE_URL}/public/v1/reports/profit-loss?start=2026-01-01&end=2026-12-31`,
      { headers },
    );
    check(pnl, { "public pnl 200": (r) => r.status === 200 });
  }
  sleep(0.2);
}
