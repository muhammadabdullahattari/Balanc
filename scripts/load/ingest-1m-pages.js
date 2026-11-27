import http from "k6/http";
import { check } from "k6";

// G6: Sustained 1,000,000 pages/day = ~11.6 pages/sec average, 60+ pages/sec peak burst.
export const options = {
  scenarios: {
    sustained_million_page_rate: {
      executor: "constant-arrival-rate",
      rate: 30, // 30 2-page docs/sec = 60 pages/sec (5.18M pages/day peak headroom)
      timeUnit: "1s",
      duration: "5m",
      preAllocatedVUs: 40,
      maxVUs: 120,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<350"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const INBOUND_SECRET_SIG = __ENV.INBOUND_HMAC_SIG || "";
const TARGET_BUSINESS_ID = __ENV.BUSINESS_ID || "";

export default function () {
  if (!TARGET_BUSINESS_ID) {
    const res = http.get(`${BASE_URL}/health/live`);
    check(res, { "health ok": (r) => r.status === 200 });
    return;
  }
  const payload = JSON.stringify({
    business_id: TARGET_BUSINESS_ID,
    message_id: `load-${__VU}-${__ITER}-${Date.now()}`,
    from: "billing@supplier-load.test",
    to: "bills+load@in.ledgerpilot.app",
    subject: `Invoice LOAD-${__VU}-${__ITER}`,
    text: "Vendor: Load Supplier\nSubtotal: 100.00 USD\nTax: 10.00 USD\nTotal: 110.00 USD",
    spf_dkim_passed: true,
  });
  const res = http.post(`${BASE_URL}/api/v1/webhooks/inbound-email`, payload, {
    headers: {
      "Content-Type": "application/json",
      ...(INBOUND_SECRET_SIG ? { "X-Inbound-Signature": INBOUND_SECRET_SIG } : {}),
    },
  });
  check(res, { "ingested 201": (r) => r.status === 201 });
}
