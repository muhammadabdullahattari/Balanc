# Repeatable 6-Minute End-to-End Product Demo Script

This walkthrough demonstrates the complete **LedgerPilot AI-Run Finance Office** loop from workspace sign-up to a cryptographically verifiable full-quarter auditor pack.

## Automated Verification
- Playwright walkthrough spec: `frontend/tests/browser/demo-walkthrough.spec.ts`
- Offline pack signature check: `python scripts/verify_pack.py <downloaded-quarter-pack.zip>`

---

## Step-by-Step Live Walkthrough

1. **Workspace Onboarding & Timezone Lock (`0:00 – 0:45`)**
   - Sign in and create or open a business workspace (e.g., `Acme Studio`, `USD`, `Asia/Karachi (UTC+05:00)` fixed IANA dropdown).
   - Land on **Overview (`/`)** to see today's cash position, empirical 13-week cash forecast confidence band (`P10`–`P90`), and plain-language action cards.

2. **Connect Live Bank Feed & Match a 3-Invoice `$1,240` Settlement (`0:45 – 2:00`)**
   - Open **Connected feeds (`/connectors`)**, connect a Plaid or Stripe Financial Connections bank feed, and click **Sync new transactions**.
   - Open **AI assistant -> Bank matching (`/ai-assistant`)** or `POST /banking/match-proposals`: observe `$1,240.00` automatically matched across 3 open invoices (`$400 + $500 + $340`) with one-click human approval.

3. **Supplier Bill Arrives Over Purchase Order Tolerance (`2:00 – 3:30`)**
   - Open **Purchase orders (`/purchase-orders`)** and issue `PO-2026-04` for `$5,000.00` (`2%` price tolerance).
   - Forward or ingest supplier invoice `BILL-409` for `$5,400.00` (`+8.00%` over PO) via `/webhooks/inbound-email` or **Snap a mobile receipt (`/capture`)**.
   - Observe two automatic protections:
     1. Re-uploading the same PDF on Drive or mobile is **deduplicated** (`document_fingerprints`), never double-drafted.
     2. Because `+8.00% > 2.00%`, the bill is held in **`pending_owner_approval`** and surfaces immediately on the owner's **Overview** decision queue, with exact pixel bounding-box highlights in **Documents (`/evidence`)**.

4. **Plain-Language Cash Warning & Causal Profit Q&A (`3:30 – 4:45`)**
   - On **Overview (`/`)** and **Reports (`/reports`)**, ask *"Why is my profit lower this month?"* — LedgerPilot cites the exact expense accounts and notable bills driving the period-over-period change, and clicking any report figure opens the **Source entries** drilldown modal.

5. **One-Click Full-Quarter Verifiable Tax & Auditor Pack (`4:45 – 6:00`)**
   - Open **General ledger (`/ledger`)**, pick the quarter (`2026 Q4`), and click **Export quarter auditor pack**.
   - Verify the downloaded ZIP offline with zero database access:
     ```powershell
     python scripts/verify_pack.py ledgerpilot-auditor-pack-2026-Q4.zip
     ```
     Output confirms `"valid": true` over `Ed25519` `manifest.sig` and every file's SHA-256 hash.
