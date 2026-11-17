import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";
import { SharedArray } from "k6/data";

const origin = __ENV.LOAD_ORIGIN;
if (
  __ENV.LEDGERPILOT_DISPOSABLE_TARGET !== "yes" ||
  !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin || "")
) {
  throw new Error("Use a disposable loopback target or local tunnel and explicitly opt in.");
}

const fixtures = new SharedArray("isolated accounts", () => JSON.parse(open(__ENV.FIXTURES)));
if (!fixtures.length) throw new Error("Provide disposable access cookies and workspace IDs.");

const target = Number(__ENV.LOAD_TARGET || 10000);
const mode = __ENV.LOAD_MODE || "rps";

export const rate503 = new Rate("rate_503");
export const asyncJobSuccessRate = new Rate("async_job_success_rate");
export const jobPollDuration = new Trend("job_poll_duration");

export const options = {
  scenarios: {
    spike:
      mode === "concurrency"
        ? {
            executor: "ramping-vus",
            startVUs: 0,
            stages: [
              { duration: "1m", target: 100 },
              { duration: "5s", target },
              { duration: "2m", target },
              { duration: "1m", target: 0 },
            ],
          }
        : {
            executor: "ramping-arrival-rate",
            startRate: 100,
            timeUnit: "1s",
            preAllocatedVUs: 100,
            maxVUs: 10000,
            stages: [
              { duration: "1m", target: 500 },
              { duration: "5s", target },
              { duration: "2m", target },
              { duration: "1m", target: 100 },
            ],
          },
  },
  thresholds: {
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    rate_503: ["rate<0.01"],
    ...(mode === "rps" ? { dropped_iterations: ["count==0"] } : {}),
  },
};

const SAMPLE_PDF_PAYLOAD = "%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF";

export default function () {
  const row = fixtures[(__VU - 1 + __ITER) % fixtures.length];
  const cookies = { "__Host-lp_access": row.access_token };
  const headers = {
    "Content-Type": "application/json",
    ...(row.csrf_token ? { "X-CSRF-Token": row.csrf_token } : {}),
  };

  // Mixed profile selection: 60% Reads, 15% Writes, 15% Async Jobs (202), 10% Streaming Uploads
  const rand = Math.random();

  if (rand < 0.6) {
    // === READ WORKLOAD (Cached Dashboard or Accounts Listing) ===
    const readAccounts = Math.random() < 0.5;
    if (readAccounts) {
      const res = http.get(
        `${origin}/api/v1/businesses/${row.business_id}/accounts?limit=25`,
        { cookies, headers, tags: { name: "GET workspace accounts" } }
      );
      rate503.add(res.status === 503);
      check(res, {
        "read accounts status 200": (r) => r.status === 200,
      });
    } else {
      const res = http.get(
        `${origin}/api/v1/businesses/${row.business_id}/reports/dashboard`,
        { cookies, headers, tags: { name: "GET workspace dashboard" } }
      );
      rate503.add(res.status === 503);
      check(res, {
        "read dashboard status 200": (r) => r.status === 200,
      });
    }
  } else if (rand < 0.75) {
    // === WRITE WORKLOAD (Pessimistic-Locked Workspace Operation < 5ms hold) ===
    const uniqueSuffix = `${__VU}_${__ITER}_${Date.now().toString(36)}`;
    const payload = JSON.stringify({
      code: `AC_${uniqueSuffix}`.substring(0, 30),
      name: `Account ${uniqueSuffix}`,
      kind: "expense",
      control: null,
    });

    const res = http.post(
      `${origin}/api/v1/businesses/${row.business_id}/accounts`,
      payload,
      { cookies, headers, tags: { name: "POST create account" } }
    );
    rate503.add(res.status === 503);
    check(res, {
      "write account status 201": (r) => r.status === 201,
    });
  } else if (rand < 0.9) {
    // === 202 ASYNC BACKGROUND JOB WORKLOAD (Report Export & Polling) ===
    const exportPayload = JSON.stringify({
      report_type: "balance_sheet",
      format: "json",
      as_of: new Date().toISOString().slice(0, 10),
    });

    const res = http.post(
      `${origin}/api/v1/businesses/${row.business_id}/reports/export`,
      exportPayload,
      { cookies, headers, tags: { name: "POST async report export" } }
    );
    rate503.add(res.status === 503);
    const accepted = check(res, {
      "async export returns 202": (r) => r.status === 202,
    });

    if (accepted) {
      try {
        const body = JSON.parse(res.body);
        const jobId = body.job_id;
        const pollStart = Date.now();
        let jobDone = false;

        // Poll up to 5 times for completion
        for (let attempt = 0; attempt < 5; attempt++) {
          sleep(0.1);
          const pollRes = http.get(
            `${origin}/api/v1/businesses/${row.business_id}/jobs/${jobId}`,
            { cookies, headers, tags: { name: "GET poll background job" } }
          );
          rate503.add(pollRes.status === 503);

          if (pollRes.status === 200) {
            const pollBody = JSON.parse(pollRes.body);
            if (pollBody.state === "succeeded" || pollBody.state === "completed") {
              jobDone = true;
              break;
            }
          }
        }
        jobPollDuration.add(Date.now() - pollStart);
        asyncJobSuccessRate.add(jobDone);
        check(null, {
          "async job completed successfully": () => jobDone,
        });
      } catch (e) {
        asyncJobSuccessRate.add(false);
      }
    } else {
      asyncJobSuccessRate.add(false);
    }
  } else {
    // === STREAMING EVIDENCE UPLOAD (Chunked Fernet / Bounded RAM) ===
    const uploadRes = http.post(
      `${origin}/api/v1/businesses/${row.business_id}/evidence/upload?filename=proof_${__VU}_${__ITER}.pdf`,
      SAMPLE_PDF_PAYLOAD,
      {
        cookies,
        headers: {
          "Content-Type": "application/pdf",
          ...(row.csrf_token ? { "X-CSRF-Token": row.csrf_token } : {}),
        },
        tags: { name: "POST streaming evidence upload" },
      }
    );
    rate503.add(uploadRes.status === 503);
    check(uploadRes, {
      "streaming upload status 201": (r) => r.status === 201,
    });
  }

  if (mode === "concurrency") sleep(1);
}
