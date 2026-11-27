# LedgerPilot P4 implementation plan

The [consolidated P4 plan](Next_Phases_Implementation_Plan.md#p4-specialized-enterprise-delivery) also covers SRS inventory and group accounting in P4-E.

P4 is the funded enterprise scale, durability and migration track. It begins only after production workloads and customer demand justify the investment. The P4 numbers below are workload goals from the SRS, not current capacity claims; workload definitions and infrastructure must be agreed before implementation.

## Entry gate

Collect measured P2/P3 traffic and document volumes, customer commitments, supported regions, legal/data-residency requirements, RTO/RPO, budget, incident staffing and restore evidence. Define benchmark fixtures and acceptance thresholds for throughput, latency, isolation and cost before provisioning the target architecture.

## Implementation sequence

| Package | Build | Acceptance gate |
|---|---|---|
| **P4-A · Capacity and isolated bulk ingestion** | Architect dedicated ingestion/worker cells, durable queue admission, tenant fairness, prioritization, backpressure and live-work isolation. Target millions of pages/day and a 500,000-page backlog while foreground traffic continues. | Production-shaped load, stress, spike, soak and recovery runs demonstrate agreed throughput/latency/error/cost thresholds without violating live-traffic SLOs. Publish actual results and limits; do not claim success based on emulator/demo infrastructure. |
| **P4-B · Large-ledger online migrations** | Add expand/contract schema patterns, online backfills, dual-read/write only when needed, compatibility windows and staged rollouts for large financial tables. | Rehearse against production-shaped volumes with measured write/read availability, correctness, rollback/forward-fix and ledger invariants. Zero downtime is reported only if measured acceptance demonstrates it. |
| **P4-C · Regional resilience and residency** | Implement selected regional cells, data placement/residency controls, encrypted replication, regional failover and recovery operations. | Legal approval for regions/data classes; destructive regional-loss exercises; independently measured RTO/RPO and consistency. A zero-data-loss objective is accepted only if tests prove it for the stated failure model; otherwise document achieved RPO. |
| **P4-D · Enterprise operations and cost controls** | Add capacity budgets, tenant quotas, autoscaling boundaries, cost attribution, AI cost dashboards, operational drills and customer-facing service reporting. | Alert/runbook ownership, failover and restore evidence, tenant fairness and cost thresholds are demonstrated under expected and peak scenarios. |

## Release gate

Funded infrastructure, 24/7 or explicitly bounded operational coverage, legal approvals and tested rollback/recovery are required. Publish only measured capacity, availability, migration and recovery results with workload, region, date and test methodology. Revalidate targets as the architecture or customer workload changes.

See [feature-by-feature delivery plan](P3_P4_Feature_Implementation_Plan.md) for the full implementation order, dependencies and acceptance checks.

## Application & Schema Implementation Record — 27 November 2026

All P4 application modules, RLS-enforced PostgreSQL tables (`0040_w0_ai_features_foundation.py`), FastAPI endpoints (`/api/v1/businesses/{business_id}/p4/*`), and scoped standalone `Advanced` sidebar pages (`frontend/src/features/automation-routes.ts`, `frontend/src/features/p3-automation.tsx`) are implemented and verified in local and CI suites:

- **P4-D (`Usage limits` — `/usage-limits`):** `app.workspace_operational_quotas` — daily document-page allowances, storage quotas, monthly AI spending caps, alert thresholds, noisy-neighbor spike testing, plus scoped governance sub-tabs for **Data encryption** and **Single sign-on & region**.
- **P4-B (`Data migration` — `/data-migration`):** `app.online_ledger_migrations` — expand/backfill/verify/contract background updates across journal lines, journals, invoices/bills, and bank rows with pre/post trial-balance tie-out verification and pause/complete/undo transitions.
- **P4-A (`Bulk import` — `/bulk-import`):** `app.bulk_ingestion_batches` — isolated background vs. live-priority document queues, per-tenant concurrency slots, foreground latency monitoring, and batch cancellation.
- **P4-C (`Disaster recovery` — `/disaster-recovery`):** `app.regional_dr_drills` — primary/standby region topology, monotonic fencing tokens preventing split-brain writes, failover/failback drills, and ledger/evidence consistency digests.
- **P4-E1 (`Inventory` — `/inventory`):** `app.inventory_items` & `app.inventory_movements` — warehouse stock tracking, FIFO layer and weighted-average costing, landed shipping/customs cost allocation, negative-stock guards, low-stock alerts, and automatic COGS review drafts on sale.
- **P4-E2 (`Group reporting` — `/group-reporting`):** `app.consolidation_entities` & `app.consolidation_runs` — multi-subsidiary ownership, closing/average FX rate translation (CTA), minority/NCI share calculation, and intercompany receivable/payable & revenue/expense eliminations.

Full verification details are recorded in [P3_P4_AI_and_Audit_Implementation_Record.md](P3_P4_AI_and_Audit_Implementation_Record.md). Funded multi-region infrastructure load certification remains a separate operational gate.
