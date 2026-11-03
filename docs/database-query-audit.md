# Database error handling and N+1 audit

Date: 3 November 2026. Scope: the existing local P1 FastAPI/PostgreSQL modular monolith. This audit does not change delivery phases, authentication, financial policy, public API schemas or schema revision 0022.

## Findings and repairs

- Bank CSV imports queried existing external IDs for each row. They now load the selected account's IDs once. In-file duplicate detection, inconsistent-content rejection, business serialization and database uniqueness remain enforced.
- Document validation fetched category/tax accounts per source line. It now loads accounts once and applies the same active/type/control checks in memory. Financial import previews reuse tenant-scoped account and party inputs instead of querying them again for each source/opening row.
- Payment/refund allocation validation fetched each document/credit note separately. It now loads selected IDs once, preserving ownership, chronology, credit and available-balance checks.
- Statement reconciliation used two reads per outstanding item. It now fetches the selected `(journal_id, position)` values and consumed journals in two scoped queries; unknown, already-matched and duplicate items still fail. The consumed-source query now also includes journals directly linked to imported bank rows (including category/transfer postings), closing a gap that could otherwise let an already-cleared movement be presented as outstanding to force a tie-out.
- Dashboard trends called the financial report repeatedly for each month. One conditional aggregate now supplies all month-end balances. Explicit local inputs supply account, source, allocation and opening-policy data for cash calculations. Each checkpoint still applies its own accounting date; source inputs are not cached on sessions or shared between requests. Opening cash calculations also use pre-indexed credit/refund totals instead of repeatedly scanning all sources.
- Cash forecast validation, bank splits/reclassification and opening cash-policy components now batch their account reads.
- Backdated loan repayment checks queried aggregate principal for each later date despite already loading the movements. They now compute same-day totals and later balances from that one ordered history, preserving the no-negative-future-principal rule.
- Reminders issued an insert per overdue invoice. They now insert at most 500 notification rows per statement, retain database deduplication and count actual inserted rows.
- Evidence-worker database errors could reach the CLI as an unhandled database traceback. Both worker transactions still roll back automatically; failures now become a sanitized `ERR_DEPENDENCY`. The CLI prints a safe error and exits nonzero. An existing claim is recovered through its lease; a failed/uncertain completion is never declared successful or blindly replayed.
- Dashboard date boundaries now reject `date.min`/`date.max` before predecessor/successor arithmetic can overflow.

## Error and security boundaries

HTTP database work uses the function-scoped `db_session` dependency with `sessionmaker.begin()`. This covers statement, flush and commit errors, including commit-time failures after the endpoint returns. SQLAlchemy failures propagate to the existing centralized handler: constraint/transaction conflicts return 409; other database failures return 503. Responses and application logs contain safe codes/correlation metadata, not SQL, parameter bindings or private source values. Known posting conflicts are translated narrowly and then rolled back; they are not swallowed.

Worker claim and completion transactions, upload admission, rate counters, readiness and local administration were inspected. Bounded pools, connection/statement/lock/idle-transaction timeouts, parameter hiding, parameterized SQL, runtime-role validation and transaction-local RLS context remain in place. Administrative identifiers use `psycopg.sql.Identifier`. No additional provider, cache, queue or migration was introduced.

Wrapping every individual query in `try/except` would not add SQL-injection protection and can leave an aborted transaction or hide a partial write. Failures are caught where the entire transaction can be rolled back and reported safely. A programmer catching an expected optional failure while continuing must use an explicit savepoint and a narrowly documented exception policy.

## Verification

Final verification passed **150 backend tests in 450.45 seconds, with zero failures/errors/skips**, including all 12 new audit cases and all local service/bootstrap profiles. Ruff, formatting, mypy (91 source files), OpenAPI (126 paths), requirement traceability (104 references), generated frontend API types and Git whitespace checks passed. Full evidence is in [the audit results](contracts/database-query-audit-results.json). Twelve new regressions exercise real PostgreSQL and HTTP transactions, including failed flush, commit-time flush, statement timeout, sanitized logs/responses, subsequent pooled connection reuse, source-conflict rollback, batched lookup counts, accounting totals, backdated loan guards and notification deduplication. The reconciliation check also uses the actual local upload/scanner/leased worker and rejects a category-generated journal presented as an outstanding item.

Observed budgets: document validation 1/60 lines = 2/2 SELECTs; financial preview 1/25 rows = 3/3; bank import 1/100 rows = 13/13; payment allocation 1/7 sources = 31/31; accrual dashboard 2/12 months with 1/5 accounts = 27/27; cash dashboard = 32/32; reconciliation item lookups plus ledger aggregate 1/6 items = 3/3; loan repayment 0/5 later dates = 20/20. Six overdue invoice notifications use one INSERT; repeat review creates zero duplicates. These are round-trip budgets, not production latency benchmarks.

Counters count SQL SELECT round trips at the SQLAlchemy engine; they do not count internal statements in PostgreSQL functions or pretend to measure latency/throughput. Endpoint counts exclude the separate rate-limit engine. Query-count comparisons use small and larger permitted pilot inputs. Production EXPLAIN/load measurements are a separate capacity gate.

## Reviewed patterns retained deliberately

- Journal detail loads its one journal's lines once; its serialization comprehension is not an N+1 query. Ledger pages already join journals and lines, loan lists already group principal balances, bank pages batch reverse peers, and model source lines are JSONB values rather than lazy ORM relationships.
- Financial migration apply intentionally executes at most 25 financial commands serially in one outer transaction. Later commands may depend on earlier documents, balances and version increments. Each still goes through canonical authorization, idempotency and posting invariants. This is bounded sequential command execution, not an unbounded list/detail read path; bulk bypassing the posting gateway would weaken safety.
- Export keyset pages and the fixed set of rate buckets retain bounded statements and lock ordering. They are not rewritten as an unbounded single query.
- The canonical PostgreSQL posting function retains its in-database line validation/insertion loop, bounded to two to 200 lines, and deferred integrity triggers. The application query budgets do not assert that this function performs only one internal SQL statement. Profiling or replacing these internal indexed checks with set-based validation requires a separately tested forward migration; no posting-safety checks were removed to improve a round-trip counter.
- Existing primary/unique/tenant indexes cover ID lookups, bank external IDs, source references, allocation dates and loan histories. No speculative index or applied-migration rewrite was made. Large-data planner/index tuning remains dependent on representative measurements.

## Limits

Verification applies to the local pilot and tested paths, not every possible future SQL statement or ten-million-user capacity. Constant round trips do not make growing source histories free: cash reports retain tenant source data in memory during calculation and use the pilot's quotas/timeouts; large production histories need measured server-side aggregation or background report jobs. Existing transaction isolation remains PostgreSQL READ COMMITTED; the audit does not claim that a multi-query report is a cross-statement MVCC snapshot under concurrent writers. Production isolation/load/HA sign-off remains a deployment-stage requirement. No commit, push or deployment is part of this work.
