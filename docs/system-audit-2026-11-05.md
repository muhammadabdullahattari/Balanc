# Implemented-system audit — 5 November 2026

## Verdict and evidence boundary

The modular monolith is appropriate for the approved local pilot. It is not fully optimized or production-qualified. This review combines fresh inspection of database/session boundaries, exports, scanner protocol, browser transport, upload UX and deployment code with the immediately preceding feature-wide regression/audit evidence. All recorded source fingerprints in `contracts/qa-findings-results.json` still match. Its 163 backend, 40 component, nine transport and 11 browser passes are **prior verified results**, not newly rerun tests. No application code was changed in this audit.

This is not a claim of exhaustive penetration testing or line-by-line verification of every feature. Large-data plans, concurrent-report consistency, hostile dependency timing, actual cloud permissions, current dependency-advisory feeds and manual accessibility/browser checks were not freshly exercised. No new cross-tenant exploit or financial-posting bypass was demonstrated.

## Findings requiring action

### A1 — High, conditional deployment data durability

`deploy/aws/deploy-api.sh` removes the old API container and starts the replacement without an evidence volume mount. `documents/storage.py` stores encrypted evidence on the local filesystem. If hosted configuration uses that adapter inside the container, replacement removes access to originals; PostgreSQL metadata alone cannot restore them. Configure persistent storage with permissions for UID 10001, backup and restore coverage, and identical mounts on replacement and rollback. Alternatively implement the approved private object-store adapter before deployment. Verify upload → replace container → download and hash verification. This is a deployment defect conditional on using the supplied filesystem adapter, not evidence that local originals have been lost.

### A2 — High, deployment success can misrepresent readiness

The deployment helper accepts Docker health, while `backend/Dockerfile` checks `/health/live`. A process can be alive while database credentials, schema or connectivity are unusable. Gate traffic/deployment success on readiness as well as liveness. The helper stops/removes the old instance before installing its rollback trap and starting the new instance; this creates downtime and an unprotected interruption window. Install recovery handling before destructive steps and stage the candidate on a separate port behind the proxy. Verify a bad database configuration cannot be declared successfully deployed, and test rollback health. The existing helper is not a zero-downtime release mechanism.

### A3 — Medium, scanner has per-operation timeouts but no total deadline

`documents/storage.py:scan` reads replies one byte at a time with a 10-second socket timeout. A slow peer returning a byte before each timeout can retain the worker far longer than 10 seconds; the 4,096-byte cap limits memory, not total duration. Add a monotonic overall deadline covering connect, command writes, stream writes and replies. Use bounded buffered reads and remaining-time socket limits. Preserve response-ID validation and positive clean verdicts. Test a slow-drip peer and stalled writes; a malformed response must never become a clean result. This is availability hardening for the private scanner dependency, not a proven public remote-code-execution issue.

### A4 — Medium, evidence-export limits apply after substantial allocation

`workspace/exports.py:pack` loads statements, evidence links, opening balances/corrections/policies and associated objects into Python collections. It then builds JSON files in memory, checks the combined 10 MiB limit near the end, and builds another in-memory ZIP. The 20-file/5 MiB original limit does not cap metadata memory before loading/serialization. Add SQL cardinality checks and incremental byte accounting before accumulating each artifact; preserve explicit quota rejection rather than silently truncating evidence. For larger exports use a bounded background job and persistent storage. Measure peak RSS on large metadata sets, including requests rejected by the final size limit.

### A5 — Medium, export generation holds the business write lock

`workspace/resumable.py:create` locks the business before generating reports, decrypting originals and assembling the archive. Financial commands also serialize on that business. Slow storage or a large pack can therefore block unrelated financial writes until lock timeouts occur. Preserve a consistent versioned snapshot, but shorten the write-lock interval; use a dedicated report transaction or durable export job with an explicitly chosen consistency contract. Test export generation concurrently with posting and verify both exact contents and bounded write latency. Do not simply remove the lock without replacing its consistency guarantee.

### A6 — Medium, multi-query report consistency remains a design gap

The engine uses the database's normal isolation configuration; report inputs are loaded through multiple statements. Under the documented READ COMMITTED profile, concurrent commits can make related report sections reflect different instants. The new outstanding-balance CTE is coherent within its own statement, but does not make the whole dashboard/export a single snapshot. Choose and test a report-only repeatable-read transaction or equivalent snapshot mechanism, including authorization and retry behavior. Do not change financial command isolation globally without measuring contention.

### A7 — Low/medium, selected evidence detail can remain stale

`frontend/src/features/evidence.tsx` polls the evidence list, while the selected detail query has no polling interval. When a worker completes extraction, the list can update while the open detail panel still shows earlier extraction state. Refresh/invalidate the selected detail when its list status/version changes, or poll only while that item is processing. Stop polling for terminal states and hidden views. Verify a real upload progresses to extracted/manual-review state without closing the panel or reloading the page.

## Feature coverage and retained controls

- Identity/password/MFA/Google integration: existing owned sessions, refresh rotation/replay revocation, browser-bound verification, password hashing/bulkhead and CSRF protections have prior regression coverage. Live Google consent and hosted cookie/proxy behavior still need deployed verification.
- Tenancy/roles: runtime-role validation, transaction-local scope, membership checks, RLS and composite ownership constraints remain central controls. No authorization should be delegated to frontend visibility.
- Ledger/periods/reversals: exact decimals, atomic balanced posting, immutable history, period locks and idempotency remain covered by the existing real-database tests. Keep the canonical posting gateway.
- Sales/purchases/receipts/payments/credits/refunds/advances/loans: prior golden workflows cover settlement, chronology and control reconciliation. No new bypass was demonstrated in this review.
- Bank import/matching/transfers/reclassification/reconciliation: explicit 500-row insert and idempotent reimport checks passed previously; shared batch validation and business serialization remain appropriate.
- Reports/forecast/dashboard/tax support: query counts improved and source limits reject overflow. A4–A6 remain relevant to scale and consistency. Forecasting remains the documented deterministic pilot model, not a calibrated predictive model.
- Documents/OCR/duplicates/provenance: positive scanning, private encrypted originals, review-before-posting and worker leases remain. A1, A3 and A7 affect durability, availability and UX.
- Opening balances/imports/close/reminders: reviewed atomic workflows and role separation have prior coverage. Keep import compilation batched and execution through canonical financial services.
- Exports/privacy/audit: signed manifests and safe operational logs help integrity and diagnosis; signatures do not establish source truth or completeness. Export resource usage and consistency need A4–A6. Audit staged logs are not proof of commit.
- Frontend: canonical transport, query cache, explicit error states and production CSP remain. Automated checks do not close the previously deferred manual screen-reader, file-picker, responsive-layout and supported-browser matrix checks.
- CI/deployment/operations: locked dependencies, test gates and container restrictions are useful foundations. A1–A2 and actual restore/failover/alert/ingress checks must close before production claims.

## Optimization priorities

1. Close deployment durability/readiness gaps before hosting financial evidence.
2. Bound scanner wall-clock time and export memory; shorten export write-lock occupancy.
3. Define a coherent report snapshot and test concurrent writes against it.
4. Refresh selected evidence details only while processing; avoid permanent broad polling.
5. Capture `EXPLAIN (ANALYZE, BUFFERS)` on representative isolated datasets before adding indexes. Measure p50/p95/p99, RSS, pool waits and lock waits; constant query counts alone do not prove low latency.
6. Size total PostgreSQL connections across processes and replicas: default capacity is up to ten application connections plus two rate-limit connections per process, before workers and administration. Horizontal replication multiplies this budget.
7. Keep sequential financial commands where they enforce invariants. Optimize read aggregation, background export work and measured hot paths before introducing caches, Redis or microservices.

Measured prior service-level read counts: dashboard accrual 13 (previously 27), cash 18 (previously 32), payment allocation command 23 (previously 31). These are not production capacity guarantees. The previously recorded moderate development-tool advisory requires a fresh advisory/compatibility review; it was not rechecked against live feeds in this audit.

## Delivery status

Audit findings recorded; application fixes are not applied in this audit. Phase 9 remains next, with A1–A2 as explicit deployment blockers for the supplied topology. Prior phase completion does not imply production scale, regional recovery or independent penetration-test sign-off. No commit, push or cloud deployment performed.


## Remediation follow-up (5 November 2026)

The user authorized applying A1-A7. See [implementation and hosted setup](deployment-audit-fixes.md), [ADR-016](architecture/ADR-016-report-snapshots-and-pilot-deployment.md), and [verification results](contracts/deployment-audit-results.json). The original findings above are retained as the audit-time record; current remediation status supersedes the original "not applied" verdict.

Clarification to A6: the current authorization helper already acquired a shared business lock for GET requests. That protected canonical financial writes in that request path, but incurred contention and was not a general snapshot guarantee for all report inputs. Remediation replaces that path with explicit snapshot isolation and short authentication activity transactions; it does not claim every prior HTTP financial report was demonstrably inconsistent.
