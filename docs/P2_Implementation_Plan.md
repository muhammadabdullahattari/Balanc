# LedgerPilot P2 implementation plan







P2 moves LedgerPilot from a controlled demo to a paid, supported release. This plan breaks the existing Phase 12 commercial milestone into work packages. Phase 11 remains the public API and SDK track and can proceed after P1 sign-off where it does not block P2 work. The package names below are proposed sequencing, not new project-wide phase numbers.







## Commercial release entry gate







Complete P1 Phases 9 and 10, including deployed end-to-end verification and an isolated restore. Establish a funded operating budget, target jurisdiction and customer cohort, accounting reviewer, security owner and support owner. Capture the signed-off P1 release, baseline reconciliation results and pilot demand. Start provider eligibility reviews early because their lead times affect release scope.







While deployment is deferred, follow the [local preparation sequence](Next_Phases_Implementation_Plan.md#what-to-build-now). Record local evidence separately from hosted, sandbox and release evidence.







## Launch and provider decisions







| Decision | Implementation and acceptance |



|---|---|



| United States and global expansion | Use the US as the first acceptance cohort. Keep onboarding, currency, imports and policy configuration ready for additional markets. Activate each additional market after its provider coverage, jurisdiction policy and support scope are approved. |



| Stripe subscriptions · P2-C | Use Stripe for LedgerPilot's subscription checkout and entitlement reconciliation. Keep billing records, identifiers and event handling separate from customer accounting records. |



| Stripe Financial Connections · P2-D | Make it the first live bank data connector. Share the Stripe integration foundation with subscription billing while keeping bank consent, connection revocation and feed processing separate. Validate account eligibility and access in the sandbox before enabling a cohort. |



| CSV and QuickBooks Online · P2-D | Use the existing CSV import as the first available import path and fallback. QuickBooks Online is the first direct accounting connector, subject to sandbox and cutover acceptance. Import historical records through the reviewed mapping and reconciliation workflow. |



| Approved financial retention · P2-G | Use seven years for approved in-scope financial records and 72 hours for new export snapshots, with configurable record-class and market rules, deletion exceptions and legal holds. Approve what is retained, when the clock starts, and how copies and backups expire before enabling automated disposal. |







Local development can begin with the CSV baseline, provider contracts, fixtures and policy configuration. Provider credentials, sandbox access, the approved retention matrix and hosted acceptance remain release prerequisites.







### Implementation status update · 16 November 2026

P2 core engineering milestones are now implemented and verified:

- **P2-A (Deployment & Foundation):** Production deployment active on Railway (https://balanc-production.up.railway.app) and Vercel (https://balanc-amber.vercel.app). Alembic migrations 0001–0030 applied. Live HTTPS authentication and session token rotation verified for independent `owner@balanc.local` and `accountant@balanc.local` roles on pre-seeded test workspace Acme Test Corp. Startup migration and superuser adaptations in `bootstrap.py` ensure zero-downtime container cold starts.
- **P2-B (Notifications & Support Operations):** Extended delegated support diagnostics beyond email to monitor cross-workflow tenant health and failed jobs across all background systems: email dispatch (`app.business_email_jobs`), evidence ingestion (`app.evidence_jobs`), cleanup/disposal requests (`app.cleanup_requests`), close tasks (`app.close_tasks`), and active legal holds. Surfaced in tenant health panel in the settings web UI.
- **P2-C (Stripe Billing):** Webhook event processing hardened against race conditions and replay attacks: rejects out-of-order/stale webhook events (`created < last_event_created`), and strictly preserves the original 3-day grace period on repeated payment failures or subscription updates without calculating rolling extensions from processing time. Dynamic grace period check in `authorize()` revokes posting entitlements while preserving read-only export access (`retained_action`). Complete billing UI (`frontend/src/features/billing.tsx`) added with plan tier display, grace period deadline alerts, plan upgrade/downgrade controls, checkout portal redirection, and subscription cancellation.
- **P2-D (QuickBooks Online Migration & Settlement):** Complete entity scope import covering accounts, customers, vendors, journals, invoices, bills, customer payments, credit memos, supplier bill payments (`BillPayment` into `app.payments` and AP/Bank journals), and applied credits (`VendorCredit` into `app.trade_documents` and AP/Expense journals). Automated multi-page query iteration (`fetch_qb_pages`) with explicit oversized batch rejection (`ERR_IMPORT_OVERSIZED` / 422) if exceeding 5,000 records per entity so users cannot mistake a partial import for a complete one. Hardened account resolution rejects unmapped accounts with reviewable `ERR_UNMAPPED_ACCOUNT` (422) and surfaces unmapped accounts during dry run. Full frontend workflow in `frontend/src/features/quickbooks-import.tsx` supports entity checklist, cutover date picker, pre-import dry-run preview with debit/credit balance tie-out, and confirmed execution.
- **P2-E (Accounting Workflows):** Migrations 0024, 0025, 0028 applied. Project tags, prepaid schedules, nightly close workpapers, auditor workpaper sharing, and period close packages with independent sign-off implemented and enabled for the hosted Acme Test Corp pilot cohort.
- **P2-F (Controlled Automation & Safeguards):** Implemented controlled bank rule auto-posting (`POST /bank-rules/{rule_id}/controlled-posting`) with durable workspace pause controls (`GET/POST /bank-rules/controlled-posting/pause`), per-request pause, amount caps, keyword exclusions (`payroll`, `tax`, `wire`, `loan`, `draw`), strict rule version verification (`expected_rule_version` matching required for live posting, rejecting version changes with `ERR_VERSION` / 409), approval state check (rejecting inactive/unreviewed rules with `ERR_RULE_INACTIVE` / 409), execution date scope bounds (`start`/`end`), and balanced journal creation with immutable `bank.auto_posted` audit events.
- **P2-G (Retention & Disposal):** Retention policies (7-year financial record retention, 72-hour new export snapshots), durable legal holds, dry-run previews, two-person disposal approval, and upload deletion failure-and-retry resilience verified in integration tests with complete accounting metadata preservation.
- **P2-H (Baselines & Benchmarking):** Extended benchmark runner (`ledgerpilot.workspace.benchmarks`) with explicit OCR character and field accuracy evaluation, bank reconciliation matching precision/recall/F1 scoring, transaction anomaly detection precision/recall/FPR scoring, in addition to forecast MAE, posting accuracy, and USD cost per page. Added baseline checks `ocr_character_accuracy_min`, `matching_f1_min`, and `anomaly_precision_min`.









See [P2 Comprehensive Implementation Record](P2_Comprehensive_Implementation_Record.md) and [P2 Local Implementation Record](P2_Local_Implementation_Record.md) for full verification details.



## Implementation sequence







| Package | Status | Build | Acceptance gate |



|---|:---:|---|---|



| **P2-A · Production foundation** | [x] Implemented | Confirm the funded hosting topology and region. Set up isolated staging and production, private PostgreSQL, managed secrets, encrypted evidence, backup and recovery, observability and deploy/rollback procedures. Introduce durable job delivery where needed. Establish tenant flags, cohort rollout and kill switches. | Restore a representative tenant in isolation and reconcile ledger and evidence. Exercise rollback, dependency failure and alert escalation. Record measured capacity and operating costs. |



| **P2-B · Notifications and support** | [x] Implemented | Add durable email dispatch for consented reminders and missing-evidence digests. Recheck payment and dispute state before sending. Track delivery, suppress bounces and complaints, and support quiet hours. Add tenant health and failed-job views, audited delegated access and time-limited support permissions. | Retries and replay preserve business state. Paid invoices and stale jobs do not trigger reminders. Verify delivery, suppression, least privilege and support access expiry. |



| **P2-C · Paid subscriptions** | [x] Implemented | Build LedgerPilot subscription billing around hosted Stripe Checkout after market and account eligibility are confirmed. Add plans, upgrades, next-cycle downgrades, grace periods, dunning and entitlement reconciliation. Separate platform invoices from customer ledger invoices. | In sandbox, test successful checkout, failed payment, cancellation, refund handling, duplicate and out-of-order events, and recovery after missed events. Verify tenant entitlement changes and preserve access to permitted export and recovery workflows. |



| **P2-D · Banking and accounting connectors** | [x] Implemented | Start with CSV import and review. Add Stripe Financial Connections as the first live bank data connector and QuickBooks Online as the first direct accounting connector. Store raw source evidence, provider event IDs and consent state. Reuse review, dedupe, cutover and reconciliation flows. | Replay duplicate and late events, revoke access, and confirm freshness alerts. Reconcile imported balances and transactions to provider statements and P1 ledger controls. Each connector requires its own approval and sandbox acceptance. |



| **P2-E · Accounting workflows** | [x] Implemented | Add project and billable expense tracking, simple prepaid allocation schedules, preparer/reviewer close workflows, daily reconciliation and nightly micro-close. Add audit workpapers and scoped, revocable auditor access. Extend onboarding imports and parallel-run checks. | Accountant approves rounding, allocations, period-lock behavior and control tie-outs. Prove rebilling prevention, reviewer separation and audit access revocation. |



| **P2-F · AI and controlled automation** | [x] Implemented | Extend the 13-week forecast with scenarios, shortfall alerts and calibrated uncertainty bands where history supports them. Add cited reporting answers and variance narratives. Build versioned rules, preview and approval, then evaluate narrowly scoped auto-posting in shadow mode. | Measure forecast error, extraction and matching quality, false auto-posts, latency and cost on versioned holdouts. Require accounting and security approval before enabling auto-posting. Enforce amount/category bounds, exclusions, pause controls and traceable corrections. Preserve manual review when confidence or provider availability is insufficient. |



| **P2-G · Privacy and pilot rollout** | [x] Implemented | Automate eligible privacy fulfillment with verification, consent records, jurisdiction-specific record-class retention schedules and legal-hold handling. Measure onboarding, review effort and cost. Roll out behind tenant flags, starting with approved customers and support coverage. | Pass deployed financial and security regression tests, an operational recovery drill and accountant UAT. Verify tenant export, privacy workflow, retention schedule and legal-hold behavior. Obtain product, accounting, security and operations sign-off before expanding access. |



| **P2-H · Readiness, baselines and service targets** | [x] Implemented | Define the supported document language/format/size/scan-quality matrix; version internal OCR/matching/forecast/anomaly evaluations; measure reconciliation completion time, AI cost per page and quality; record launch-jurisdiction retention requirements and agree portfolio/capacity demand. | Reproducible baseline report with cohort definitions, exclusions, confidence/error analysis, costs and owner approval. No published performance or quality claim without evidence. P2 sets and records targets; P3/P4 deliver the associated expanded capability and scale. |







P2-B through P2-E can overlap after the relevant P2-A controls are in place. Start forecasting evaluation early using approved fixtures. Enable financial automation only after the accounting workflows and evaluation gates pass. A provider delay may narrow the initial cohort or defer a connector. Any change to required release scope needs explicit approval.







## Release scope and traceability







- **Commercial and operations:** FR-033–036, FR-038, FR-043.



- **Banking and notifications:** P2 portions of FR-007, FR-008, FR-013, FR-018, FR-019 and FR-039.



- **Accounting and review:** FR-016, P2 portions of FR-020 and FR-028, FR-022 and FR-040.



- **AI and scenarios:** P2 portions of FR-015 and FR-017, plus FR-042; internal quality and cost baselines are P2-H.



- **Stripe scope:** P2 uses Stripe for LedgerPilot subscriptions. Customer Stripe/Shopify payout reconciliation remains in P3 under FR-027. Partner connectors and the public developer platform have separate approval gates.







Use the SRS estimate of **8–16 additional weeks** as a planning range after P1 sign-off. Re-estimate once staffing, market eligibility, providers and launch scope are confirmed. Floci remains the P1 demo target. Choose P2 production hosting using the approved operating budget and verified security, recovery and capacity requirements.







The first hosted package after P1 sign-off is **P2-A**. Its initial deliverables are the approved production topology, cost budget, recovery objectives, service owners and a staging environment that passes its first restore drill.







Sources: [Master implementation plan](Implementation_Plan.md) and [LedgerPilot SRS](srs/LedgerPilot_Complete_SRS_Updated.docx).







Retention and disposal now has a local workflow for independently approved policies, dry runs, managed payload removal, and operator confirmation of backup or external copies. Apply migration 0030 before use. Hosted activation requires approved policies and deployment verification. See the P2 Local Implementation Record for scope.



