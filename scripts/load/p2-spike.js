import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";

const origin = __ENV.LOAD_ORIGIN;
if (__ENV.LEDGERPILOT_DISPOSABLE_TARGET !== "yes" ||
    !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin || "")) {
  throw new Error("Use a disposable loopback target or local tunnel and explicitly opt in.");
}
const fixtures = new SharedArray("isolated accounts", () => JSON.parse(open(__ENV.FIXTURES)));
if (!fixtures.length) throw new Error("Provide disposable access cookies and workspace IDs.");
const target = Number(__ENV.LOAD_TARGET || 10000);
const mode = __ENV.LOAD_MODE || "rps";
export const options = {
  scenarios: {
    spike: mode === "concurrency" ? {
      executor: "ramping-vus", startVUs: 0,
      stages: [{ duration: "1m", target: 100 }, { duration: "5s", target },
        { duration: "2m", target }, { duration: "1m", target: 0 }],
    } : {
      executor: "ramping-arrival-rate", startRate: 100, timeUnit: "1s",
      preAllocatedVUs: 100, maxVUs: 10000,
      stages: [{ duration: "1m", target: 500 }, { duration: "5s", target },
        { duration: "2m", target }, { duration: "1m", target: 100 }],
    },
  },
  thresholds: {
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    ...(mode === "rps" ? { dropped_iterations: ["count==0"] } : {}),
  },
};

export default function () {
  const row = fixtures[(__VU - 1) % fixtures.length];
  const response = http.get(origin + "/api/v1/businesses/" + row.business_id + "/accounts?limit=25", {
    cookies: { "__Host-lp_access": row.access_token },
    tags: { name: "GET workspace accounts" },
  });
  check(response, { "authenticated workspace read succeeds": (r) => r.status === 200 });
  if (mode === "concurrency") sleep(1);
}
