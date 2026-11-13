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
| Ten-year global retention default · P2-G | Use ten years as the default for in-scope financial records, with configurable record-class and market rules, deletion exceptions and legal holds. Approve what is retained, when the clock starts, and how copies and backups expire before enabling automated disposal. |

Local development can begin with the CSV baseline, provider contracts, fixtures and policy configuration. Provider credentials, sandbox access, the approved retention matrix and hosted acceptance remain release prerequisites.

### Local implementation update · 13 November 2026

P2 remains in progress. The local P2-F implementation now includes a scheduled, preference-aware cash-shortfall inbox worker and an explicit historical-error interval evaluation. Intervals require 20 non-overlapping calibration windows and 10 later validation windows for the same account selection; insufficient history or failed validation returns no interval. This is not evidence that any customer forecast has achieved the target coverage.

P2-G now includes versioned record-class/jurisdiction retention policies, durable legal holds, audited release, and an eligibility preview in Settings. Migration `0027` preserves policy history and restricts updates to hold-release fields. The preview uses an explicitly disclosed record-date clock and never authorizes physical deletion. Disposal still requires the approved record/copy/backup policy matrix. See the local implementation record for verification and activation instructions.

P2-E also adds scoped sharing of selected saved workpapers with a named, MFA-authenticated recipient, expiry and revocation. Sharing grants no workspace membership and does not remove independent access a recipient already holds. Broader workpaper packages remain open.

Open local work still includes P2-C subscriptions, P2-D direct connectors, P2-E broader workpapers, representative P2-H quality/cost baselines, and approved-policy privacy disposal. Billing plan prices, plan limits and failed-payment grace rules require product input; no prices or entitlements have been invented. These are not all deployment tasks and are not marked complete.

## Implementation sequence

| Package | Build | Acceptance gate |
|---|---|---|
| **P2-A · Production foundation** | Confirm the funded hosting topology and region. Set up isolated staging and production, private PostgreSQL, managed secrets, encrypted evidence, backup and recovery, observability and deploy/rollback procedures. Introduce durable job delivery where needed. Establish tenant flags, cohort rollout and kill switches. | Restore a representative tenant in isolation and reconcile ledger and evidence. Exercise rollback, dependency failure and alert escalation. Record measured capacity and operating costs. |
| **P2-B · Notifications and support** | Add durable email dispatch for consented reminders and missing-evidence digests. Recheck payment and dispute state before sending. Track delivery, suppress bounces and complaints, and support quiet hours. Add tenant health and failed-job views, audited delegated access and time-limited support permissions. | Retries and replay preserve business state. Paid invoices and stale jobs do not trigger reminders. Verify delivery, suppression, least privilege and support access expiry. |
| **P2-C · Paid subscriptions** | Build LedgerPilot subscription billing around hosted Stripe Checkout after market and account eligibility are confirmed. Add plans, upgrades, next-cycle downgrades, grace periods, dunning and entitlement reconciliation. Separate platform invoices from customer ledger invoices. | In sandbox, test successful checkout, failed payment, cancellation, refund handling, duplicate and out-of-order events, and recovery after missed events. Verify tenant entitlement changes and preserve access to permitted export and recovery workflows. |
| **P2-D · Banking and accounting connectors** | Start with CSV import and review. Add Stripe Financial Connections as the first live bank data connector and QuickBooks Online as the first direct accounting connector. Store raw source evidence, provider event IDs and consent state. Reuse review, dedupe, cutover and reconciliation flows. | Replay duplicate and late events, revoke access, and confirm freshness alerts. Reconcile imported balances and transactions to provider statements and P1 ledger controls. Each connector requires its own approval and sandbox acceptance. |
| **P2-E · Accounting workflows** | Add project and billable expense tracking, simple prepaid allocation schedules, preparer/reviewer close workflows, daily reconciliation and nightly micro-close. Add audit workpapers and scoped, revocable auditor access. Extend onboarding imports and parallel-run checks. | Accountant approves rounding, allocations, period-lock behavior and control tie-outs. Prove rebilling prevention, reviewer separation and audit access revocation. |
| **P2-F · AI and controlled automation** | Extend the 13-week forecast with scenarios, shortfall alerts and calibrated uncertainty bands where history supports them. Add cited reporting answers and variance narratives. Build versioned rules, preview and approval, then evaluate narrowly scoped auto-posting in shadow mode. | Measure forecast error, extraction and matching quality, false auto-posts, latency and cost on versioned holdouts. Require accounting and security approval before enabling auto-posting. Enforce amount/category bounds, exclusions, pause controls and traceable corrections. Preserve manual review when confidence or provider availability is insufficient. |
| **P2-G · Privacy and pilot rollout** | Automate eligible privacy fulfillment with verification, consent records, jurisdiction-specific record-class retention schedules and legal-hold handling. Measure onboarding, review effort and cost. Roll out behind tenant flags, starting with approved customers and support coverage. | Pass deployed financial and security regression tests, an operational recovery drill and accountant UAT. Verify tenant export, privacy workflow, retention schedule and legal-hold behavior. Obtain product, accounting, security and operations sign-off before expanding access. |
| **P2-H · Readiness, baselines and service targets** | Define the supported document language/format/size/scan-quality matrix; version internal OCR/matching/forecast/anomaly evaluations; measure reconciliation completion time, AI cost per page and quality; record launch-jurisdiction retention requirements and agree portfolio/capacity demand. | Reproducible baseline report with cohort definitions, exclusions, confidence/error analysis, costs and owner approval. No published performance or quality claim without evidence. P2 sets and records targets; P3/P4 deliver the associated expanded capability and scale. |

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
