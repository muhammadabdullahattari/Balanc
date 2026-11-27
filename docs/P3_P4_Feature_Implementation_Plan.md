# LedgerPilot P3 and P4 feature implementation plan

## Implementation & Verification Status — 27 November 2026

All P3 (`P3-A` through `P3-K`), P4 (`P4-A` through `P4-E2`), W0 + `AI-01`..`AI-10`, and LangGraph Financial Audit application workflows, migrations (`0039`–`0042`), APIs, and plain-language responsive web screens are implemented and verified in local and CI suites (`296` live-PostgreSQL backend integration tests, `82` frontend unit/API tests, `14` Playwright multi-viewport Chromium checks; commit `c7b8858`, CI Run `45971565380`). See:
- [P3_P4_AI_and_Audit_Implementation_Record.md](P3_P4_AI_and_Audit_Implementation_Record.md) — Complete schema, endpoint, UI, and test record.
- [AI_Features_Implementation_Plan.md](AI_Features_Implementation_Plan.md) — W0 and AI-01–AI-10 implementation tracker.
- [financial-audit-langgraph.md](financial-audit-langgraph.md) — LangGraph financial audit workflow and rule guide.
- [ADR-021](architecture/ADR-021-p3-multichannel-reminders-portal-totp-and-approved-payments.md) and [ADR-022](architecture/ADR-022-trustworthy-ai-langgraph-audit-and-ux-hardening.md) — Architecture decisions.

*Note:* Funded multi-region physical load testing (millions of pages/day hardware validation) and external third-party provider contracts/domain verifications remain operational deployment gates distinct from code completion.

## Shared delivery method

Each feature follows discovery and an approved acceptance contract, models and reversible migrations, service and worker implementation, API and role checks, automated API tests, responsive web workflow, browser integration tests, then sandbox/staging acceptance and a controlled rollout. Reuse the FastAPI modular monolith, PostgreSQL, transactional jobs, canonical React API client, existing audit records, Redis where justified and storage adapters. Introduce infrastructure only through a documented architecture decision.

Every package must record changed files, migrations, tests, known limits, provider configuration and acceptance evidence in its implementation record. Preserve tenant isolation, exact-decimal accounting, balanced atomic journals, period locks, idempotency and immutable posted history. Controlled bank-rule posting retains its approved safeguards. Payments always require human authorization. Native mobile applications remain outside this plan.

Local development can begin with isolated synthetic fixtures while deployment and external approvals are pending. Provider acceptance, destructive execution and capacity claims require their own evidence. Use the approved seven-year financial-record and 72-hour export policy where applicable, with two-person disposal approval. Preserve accounting metadata. Never silently replace a jurisdiction-specific schedule or legal hold.

## P3 delivery order

1. P3-E establishes the portfolio workflow. P3-F1 establishes versioned document and AI contracts.
2. P3-A, P3-B and P3-C extend document collection and review.
3. P3-I and P3-J expand accounting and reconciliation sources.
4. P3-F2/F3 extend the assistant and measured quality/cost reporting.
5. P3-D adds authorized payment execution after beneficiary and accounting controls.
6. P3-G and P3-K establish privacy key boundaries and regional placement.
7. P3-H measures the complete reconciliation workflow. Each completed package can release independently after its acceptance checks.

| Package | User features and implementation work | Dependencies | Completion evidence |
|---|---|---|---|
| P3-E Accountant portfolio | Authorized business list, exception counts, aging/risk filters, assignment, saved views and drill-through. Build batched aggregate queries, pagination and revocable delegation. Provide loading, empty, error and accessibility states. | Existing memberships, review queue and reports | Cross-business authorization and revoked-access tests, query budgets, an agreed hundreds-of-businesses load fixture and accountant workflow review |
| P3-F1 Document support | Publish a versioned format/language/scan-quality matrix. Route supported inputs to evaluated extraction adapters. Store field confidence, regions, model versions and provenance. Unsupported inputs enter manual review. | Existing scanning, storage and document jobs | Versioned fixtures covering noisy, rotated, multilingual, oversized and unsupported inputs; no posting from extraction alone |
| P3-A Supplier portals | Connection management, authorized vendor adapters, retrieval schedule, download history, duplicate detection and upload fallback. Isolate credentials and browser execution, restrict destinations, handle allowed MFA, rate limits and portal changes. | F1, jobs, secrets and vendor permission | Per-portal sandbox/terms acceptance, revoked credentials, changed portal, replay, timeout and hostile-page tests |
| P3-B Document chasing | WhatsApp and Slack messages with email fallback. Configure consent, recipient preference, channel order, quiet hours, locale, attempt limits, delivery history and opt-out. Recheck receipt, payment and dispute state before every send. | Existing email jobs, provider sandboxes | Signed callbacks, replay safety, no sends after resolution or opt-out, quiet-hour/timezone and delivery-failure tests |
| P3-C Voice review | Audio upload, playback, transcript correction, suggested accounting instructions and linked review draft. Apply a declared duration/size/language matrix, scanning, access and retention policy. | F1 and review drafts | Speech benchmark fixtures, abstention and correction tests, source linkage and no journal creation before authorized approval |
| P3-I1 Accounting schedules | Contract revenue recognition, accruals/reversals, payroll clearing and allocation, short-payment resolution. Model schedules and source allocations, preview journals and require controlled approval. | Existing trade, ledger and close controls | Accountant-approved journal cases, partial settlements, rounding, retries, locked periods and reversal tie-outs |
| P3-I2 Assets and FX | Fixed asset register, depreciation schedules, disposal accounting, foreign-currency amounts/rates, realized and unrealized FX and revaluation reversals. Define functional currency and rate provenance before schema changes. | I1, accountant decisions on supported methods and rate source | Golden financial cases, precision boundaries, period-end reconciliation and no retroactive mutation of posted entries |
| P3-J Commerce and portals | Import merchant Stripe/Shopify sales, payouts, fees, refunds and disputes with gross-to-net reconciliation. Add merchant enrichment, beneficiary-change checks, eligible tax verification and scoped customer/vendor portals. | Provider sandboxes, I1 and existing connector framework | Provider pagination, duplicate events, partial refunds, payout differences, portal IDOR and revocation tests. Merchant accounting remains distinct from SaaS subscription billing |
| P3-F2 Conversational accounting | Authorized read-only accounting tools, conversation history, cited report/source answers, explicit date/basis/currency context and safe refusal. Treat retrieved content as untrusted. | F1, reports, portfolio permissions | Citation correctness, tool authorization, prompt injection, incomplete-data and cross-tenant leakage tests; no arbitrary SQL or financial writes from chat |
| P3-F3 Quality and cost | Dataset registry, approved de-identified representative datasets, synthetic regression fixtures, benchmark execution and reproducible public reports for OCR, matching, forecasting and anomalies. Track model/version, latency, resource usage and measured unit cost. | P2 benchmark baseline, F1/F2 and approved data | Reviewed methodology, subgroup results, promotion gates and quality-adjusted cost comparison. Measure the 50% reduction target six months after the approved P2 baseline; report variance |
| P3-D Supplier payments | Beneficiary onboarding, payment draft, explicit approval, step-up MFA, configured dual approval, limits, expiry, status history and emergency pause. Implement one provider adapter, durable execution, stable idempotency, signed callbacks, uncertain-outcome reconciliation, returns and accounting settlement. | Approved provider and operating rules, J beneficiary checks, trade settlement | Sandbox duplicate/replay, permission revocation, changed beneficiary/amount invalidating approval, timeout ambiguity, returns and kill-switch tests. Controlled live execution needs explicit approval |
| P3-G Privacy lifecycle | Policy management, legal/regulatory holds, approved payload disposal, tenant envelope keys and key versions, external-copy request tracking and backup-expiry evidence. Keep audit and accounting metadata. | Existing retention/disposal, approved key-management boundary | Dry-run, dual approval, retry/crash recovery, hold race and isolated restore tests. Claim key destruction only within a verified boundary; external copies need confirmation |
| P3-K Enterprise access | Tenant-scoped SSO configuration, verified account linking, enrollment/recovery and administrator controls. Add regional placement registry and initial isolated cells with explicit routing and storage boundaries. | Identity design decision, approved SSO provider and regions | SSO state/nonce/replay, revoked access, tenant placement, dependency isolation and restore tests |
| P3-H Reconciliation target | Onboarding completeness checklist, missing-data chasing, progress/exception queue and escalation. Define the eligible cohort, clock start/stop and exclusions for the one-day reconciliation target. | Collection, matching, accounting and reporting packages | Observed completion distributions and failures over an approved window. Publish an attained service target only after cohort measurement |

## P4 delivery order

Define workload, budget and recovery objectives first. Implement P4-D controls and P4-B compatibility tooling before P4-A scale trials. Build P4-C on verified regional cells. P4-E can progress independently with specialist accounting review.

| Package | User features and implementation work | Dependencies | Completion evidence |
|---|---|---|---|
| P4-D Enterprise operations | Tenant usage and AI cost dashboards, quotas, queue/storage limits, service tiers, fair scheduling, scaling ceilings, operational status and incident reporting. Define owners, alerts, runbooks and resource budgets. | P3 telemetry and workload estimates | Expected/peak workload tests, quota enforcement, noisy-tenant isolation, escalation and cost reconciliation |
| P4-B Online ledger migrations | Expand/contract changes, resumable bounded backfills, compatibility windows, migration progress and forward repair. Use online index operations where appropriate and separate schema/data steps. | Production-shaped data and backup/restore evidence | Concurrent old/new application versions, write traffic during backfill, interruption/restart, lock duration, balances and measured availability. Zero downtime is a measured target |
| P4-A Bulk ingestion | Separate live and bulk worker capacity, prioritized admission, per-tenant concurrency, backpressure, queue progress and cancellation. Partition workloads by verified bottlenecks. | D budgets/fairness, B safe schema operations, evaluated extraction workers | Millions-of-pages/day target and 500,000-page backlog beside live traffic. Measure pages/sec, foreground p95/p99, errors, drain time, recovery and unit cost on funded infrastructure |
| P4-C Residency and disaster recovery | Business region selection within supported jurisdictions, placement enforcement, replicated ledger/evidence/key dependencies, fenced failover, failback and recovery status. Document transactional consistency boundaries. | P3-K cells, P3-G keys, approved RTO/RPO and regions | Regional-loss, replication-lag, split-brain prevention, restore and failback drills. Tie out ledger and evidence. Report achieved RTO/RPO; accept zero-loss claims only under the tested failure model |
| P4-E1 Inventory | Item/location movements, receipts/issues, approved costing methods, landed-cost allocation, valuation reports and inventory-to-ledger reconciliation. Define negative stock, returns and period-lock policy before implementation. | Existing purchases/sales and accounting decisions | Golden valuation, concurrent movements, returns, allocation rounding, closed periods and stock-to-ledger tie-outs |
| P4-E2 Group accounting | Entity ownership/effective dates, consolidation periods and reporting currency, intercompany matching, elimination journals and consolidated reports. Define minority interests and translation policy for supported scope. | I2 FX, entity ledger controls and specialist policy | Entity-to-group tie-outs, mismatched intercompany activity, eliminations, FX translation, restatement and authorization cases |

## Decisions and external dependencies

Select the initial supplier portals, messaging accounts, speech/extraction model evaluation candidates, payment provider, merchant test accounts, SSO provider and supported regions before their integration acceptance. Confirm accounting methods for revenue, depreciation, FX, inventory and consolidation with an accountant. Approve representative benchmark data and publication rights. Define workload mix, latency/error thresholds, RTO/RPO and operating budget before performance or recovery acceptance.

Provider fees and compute/storage costs must be measured. Open-source software and local synthetic tests support development; this plan does not assign a zero-cost production capacity guarantee. Estimate dates after each package is broken into owned tasks and its external dependencies are confirmed.

## Release completion checklist

- Backend, worker, database and browser workflows implemented with existing conventions.
- Unit, database, provider sandbox, integration, accessibility and browser checks recorded.
- Financial invariants, tenant isolation, duplicate/retry and partial-failure behavior verified.
- Role-specific UAT, configuration, migration order and recovery procedures reviewed.
- Feature flag rollout, monitoring and support ownership established.
- Implementation record lists actual evidence, unresolved dependencies and release approval.
