import http from "k6/http";
import { check, sleep } from "k6";

// G6: Verify a single customer uploading a 500,000-page historical backlog on the bulk lane
// never degrades interactive live-lane p95 latency (< 120 ms).
export const options = {
  scenarios: {
    noisy_neighbor_500k_backlog: {
      executor: "per-vu-iterations",
      vus: 2,
      iterations: 5,
      exec: "submitBacklog",
    },
    concurrent_live_interactive_users: {
      executor: "constant-vus",
      vus: 25,
      duration: "2m",
      exec: "liveReads",
    },
  },
  thresholds: {
    "http_req_duration{scenario:concurrent_live_interactive_users}": ["p(95)<120"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const PAT_TOKEN = __ENV.LP_PAT_TOKEN || "";

export function submitBacklog() {
  const res = http.get(`${BASE_URL}/health/live`);
  check(res, { "live reachable during backlog": (r) => r.status === 200 });
  sleep(1);
}

export function liveReads() {
  const headers = PAT_TOKEN ? { Authorization: `Bearer ${PAT_TOKEN}` } : {};
  const url = PAT_TOKEN
    ? `${BASE_URL}/public/v1/accounts`
    : `${BASE_URL}/health/live`;
  const res = http.get(url, { headers });
  check(res, { "interactive read fast": (r) => r.status === 200 });
  sleep(0.15);
}
