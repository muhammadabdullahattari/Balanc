# LedgerPilot Next Phases Implementation Plan

Deployment is deferred at the user's request. The immediate work is a local P2 preparation track using synthetic data and the existing React, FastAPI and PostgreSQL stack. Phase 9 deployment and Phase 10 hosted acceptance resume when the user is ready. The numbered engineering phases and the P1–P4 product releases use separate labels.

## Delivery order

| Order | Work | Release gate |
|---|---|---|
| Now | Local P1 regression and P2 preparation, in the order below | Local evidence and review. Keep changes isolated from the P1 pilot candidate. |
| Later · Phase 9 | Deploy the accepted P1 pilot candidate to verified Floci infrastructure and Vercel | Verify runtime, private networking, persistent storage, HTTPS, secrets, backup, rollback and smoke tests. |
| Later · Phase 10 | Run deployed E2E and manual acceptance | Pass accounting and tenant isolation journeys, live auth/provider checks, restore, browser matrix, accessibility and file picker checks. Record the tested deployment and sign-off. |
| After Phase 10 · Phase 11 | Build the public API and SDK track, in parallel with P2 where dependencies allow | Review scopes, keys, quotas, versioning, idempotency, signed webhooks, sandbox, docs and developer-pilot results. |
| Phase 12 · P2 | Launch the paid, supported release after the package gates pass | Funded infrastructure, provider acceptance, accountant UAT, security and recovery drills, and staffed support. |
| P3 | Expand accounting, automation, integrations and operational scale | Validate each module and provider before enabling it for an approved tenant cohort. |
| P4 | Deliver specialized enterprise accounting, scale and resilience | Funded production-shaped trials, specialist sign-off and measured service targets. |

## What to build now

See the [P2 local implementation record](P2_Local_Implementation_Record.md) for current code changes, verification and the next checkpoint.

Complete one slice at a time. Start with contracts and accounting cases, implement and verify the backend, then connect the UI and run the end-to-end workflow locally.

| Step | Implementation | Local exit evidence |
|---|---|---|
| **L1 · Protect the P1 baseline** | Record a known-good P1 revision. Run the local authentication and accounting journeys, including credentials-first and Google-first verification, recovery-code use, tenant isolation, posting, reconciliation and export. Use synthetic businesses and an isolated PostgreSQL database. | A clean build, passing relevant regression suites and a short record of the hosted checks still pending. |
| **L2 · Prepare P2-A** | Add tenant feature flags defaulting off, common job identifiers and observability fields, and a local restore drill for PostgreSQL plus evidence. Check existing job and backup components before introducing shared infrastructure. Draft recovery targets and provider interfaces. | Tenant isolation tests, worker restart and replay evidence, and a restored tenant whose ledger and evidence tie to the source. |
| **L3 · Build P2-B email delivery** | Add an email outbox to the existing database workflow and a worker using local SMTP/Mailpit. Deliver consented reminders and missing-evidence digests. Recheck consent, quiet hours and payment, dispute or evidence status at send time. Add bounded retry and a support view for failures. | No duplicate delivery during the tested retry/replay cases. Stale reminders are suppressed. Support permissions and audit trail pass. Cloud queue selection remains a hosted design decision. |
| **L4 · Build P2-E accounting slices** | Start with project tags and billable expenses, then prepaid schedules, preparer/reviewer close, daily reconciliation, audit workpapers and scoped auditor access. Follow with onboarding imports and parallel-run comparisons. | Golden journals prove correct rounding, allocation, reversals, period locks and control-account tie-outs. Check duplicate billing prevention and reviewer separation. |
| **L5 · Build P2-F/H measurement and AI slices** | Define the supported document matrix and versioned evaluation fixtures. Baseline OCR, matching, forecast error, anomaly results, reconciliation time and AI cost per page. Extend forecasts with scenarios and shortfall alerts. Add cited answers from authorized reports, then evaluate rule proposals in shadow mode. | Reproducible benchmark report with sample limits and quality thresholds. Ledger writes remain under approved posting controls. Uncertainty bands require adequate data and calibration evidence. |
| **L6 · Prepare P2-C/D/G** | Design subscription states, tenant entitlements, connector contracts and privacy workflows. Use contract fixtures to verify local transitions and ingestion. Model retention per record class, legal holds, consent and export. | Retry, duplicate and out-of-order cases pass locally. Stripe, bank feeds, accounting connectors and production privacy operations retain separate sandbox and release gates. |

The first feature slice is **durable email reminders with Mailpit** after L1 and the job controls in L2. Accounting work and evaluation can follow independently once their local prerequisites are ready.

## P2 Commercial release

| Package | Build and release evidence |
|---|---|
| **P2-A · Production foundation** | Separate staging and production, private PostgreSQL, secret management, encrypted evidence, operational alerts, feature flags, recovery and rollback. Demonstrate an isolated restore, alert escalation and rollback on the selected host. |
| **P2-B · Notifications and support** | Deploy email delivery with consent, suppression, bounce and complaint handling, delivery history and audited delegated support. Validate external delivery and permission expiry. |
| **P2-C · Stripe subscriptions** | Implement LedgerPilot's own Checkout, plans, entitlements, upgrades, downgrades, grace periods and failed-payment handling. After account eligibility and sandbox access, verify signed events, duplicate and out-of-order delivery, refunds, cancellation and reconciliation after missed events. Preserve permitted exports during billing restrictions. |
| **P2-D · Bank and accounting feeds** | Start with one approved bank/card or accounting connector. Handle consent and revocation, raw evidence, replay, freshness, mapping and cutover reconciliation. Verify provider statements against the ledger in a sandbox before cohort rollout. |
| **P2-E · Accounting workflows** | Ship the locally built workflows after deployed tests and accountant review of amounts, locks, tie-outs and audit evidence. |
| **P2-F · AI workflow** | Release forecast scenarios, shortfall alerts, cited reporting answers and variance narratives after evaluation. Shadow-test narrowly scoped auto-posting under amount and category limits, with exclusions and a pause switch. Live activation requires accounting and security approval. |
| **P2-G/H · Privacy, baselines and rollout** | Verify export and eligible privacy fulfillment. Approve jurisdiction-specific retention schedules and holds, including ten years only where applicable. Measure one-day reconciliation eligibility and duration, document coverage and AI quality/cost. Roll out behind flags with product, accounting, security and operations sign-off. |

## P3 Broader automation and integrations

Start with P2 production and recovery evidence, approved jurisdictions and a funded team. Build workflow extensions against the P2 contracts, then enable each feature after its own acceptance gate.

| Package | New feature | Acceptance |
|---|---|---|
| **P3-A** | Authorized supplier portal invoice retrieval | Use vendor-approved access and scoped credentials. Verify provenance, duplicate prevention, credential revocation and manual upload fallback. |
| **P3-B** | WhatsApp and Slack document chasing with email fallback | Verify consent, channel order, attempt limits, quiet hours, suppression after resolution and provider replay in sandboxes. |
| **P3-C** | Voice-note review | Publish supported formats, languages and duration. Keep source audio and transcript linked to a draft that an authorized person reviews. |
| **P3-D** | Human-approved supplier payment execution | Integrate one approved provider. Bind approval to beneficiary, amount and currency. Enforce step-up authentication, risk-based dual approval, limits, idempotency, audit and a kill switch. Reconcile provider confirmation, failures, returns and reversals. AI may prepare a draft. An authorized human approves every execution. |
| **P3-E** | Accountant portfolio exception dashboard | Test authorization for every business in the portfolio, revoked access, drill-through, pagination and performance for an agreed hundreds-of-businesses cohort. |
| **P3-F** | Expanded document reading, conversational accounting, public quality benchmarks and AI cost reduction | Extend the tested language, format and scan-quality matrix. Require cited answers from authorized read-only tools and safe abstention. Publish reproducible OCR, matching, forecasting and anomaly results. Target 50% lower quality-adjusted AI cost per page within six months of the P2 baseline, subject to measured quality and safety. |
| **P3-G** | Retention automation and verifiable per-business deletion | Apply P2 retention schedules and legal holds. Design per-business encryption keys, verify their destruction and document the scope of backup expiry and external copies. |
| **P3-H** | One-day reconciliation target | Use the P2 baseline to define eligible books, start and stop events, exceptions and measurement window. Validate an observed cohort before publishing a service target. |
| **P3-I** | Accounting expansion | Deliver contract revenue schedules, payroll clearing and splits, short-pay resolution, accruals and reversals, fixed assets and depreciation, and foreign currency accounting. Require module-specific journals, rounding, reversal, period-lock and control-account cases approved by an accountant. |
| **P3-J** | E-commerce and additional integrations | Reconcile customer Stripe/Shopify payouts gross to net, including fees, refunds and disputes. Add merchant enrichment, beneficiary change verification, eligible tax verification and scoped customer/vendor web portals as specified. Gate each integration on provider and security acceptance. |
| **P3-K** | Enterprise access and early regional cells | Add scoped SSO where required and regional cell isolation for an approved workload. Exercise tenant placement, isolation, dependency failure and operational recovery. |

## P4 Specialized enterprise delivery

| Package | Build | Acceptance |
|---|---|---|
| **P4-A** | Isolated bulk ingestion and workers | Demonstrate the agreed millions-of-pages-per-day workload and a 500,000-page backlog alongside live work. Measure foreground latency, error rate, throughput, backlog drain time and cost under load and recovery. |
| **P4-B** | Online large-ledger migrations | Rehearse compatible schema changes, staged backfills and rollback or forward repair on representative ledger sizes. Measure availability and verify balances. Zero downtime is a test objective. |
| **P4-C** | Regional disaster recovery and data residency | Define regions, data placement, RTO and RPO. Prove failover, restoration and ledger consistency under the agreed failure model. Regional zero data loss is a measured objective. |
| **P4-D** | Enterprise operations and cost controls | Demonstrate tenant quotas, scaling budgets, fair worker use, monitoring, incident response and recovery drills under expected and peak workloads. |
| **P4-E** | Inventory costing and group accounting | Build inventory valuation and landed cost, intercompany entries and eliminations, and group consolidation. Require specialist UAT and tie-outs from entity ledgers to consolidated results. |

## Delivery and sign-off

For every slice, record its package and requirement ID, owner, data and API impact, tenant roles, acceptance cases and actual evidence. Add migrations only when a slice needs them, and test them against existing data. Keep new capabilities off by default until their release gates pass. Provider contracts, regional availability, operating budget and exact production targets are entry decisions for the relevant package.

The existing SRS estimates P2 at 8–16 additional weeks after P1 sign-off and P3 at 6–12 additional months with team expansion. Treat these as planning inputs and re-estimate after staffing, provider access and funded scope are confirmed. P4 timing follows demand and capacity evidence.

References: [Master engineering plan](Implementation_Plan.md), [P2 packages](P2_Implementation_Plan.md), [P3 packages](P3_Implementation_Plan.md), [P4 packages](P4_Implementation_Plan.md), and [LedgerPilot SRS](srs/LedgerPilot_Complete_SRS_Updated.docx).
