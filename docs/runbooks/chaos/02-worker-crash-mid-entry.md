# Chaos Drill 02 — Worker Crash Mid-Journal Entry & Mid-Ingestion Lease

## Scenario & Fault Injected
- **Fault:** Simulated abrupt worker exception mid-transaction while writing journal headers/lines and expired worker lease (`lease_expires_at <= now()`) in `app.ingestion_queue_jobs`.
- **Expected Invariant:** Zero half-posted unbalanced journals (`sum(debit) == sum(credit)` enforced at commit), and abandoned ingestion jobs are safely reclaimed without duplicate ledger side effects.

## Observed Behavior
1. PostgreSQL transactional rollback discards uncommitted `app.journals` / `app.journal_lines` atomically; `DEFERRABLE INITIALLY DEFERRED` balance triggers prevent any partial line set from committing.
2. When a background ingestion worker crashes mid-lease, `lease_next_fair_share_job()` re-acquires the job once `lease_expires_at <= now()`, while `claim_document_fingerprint()` (`UNIQUE(business_id, content_sha256)`) guarantees idempotent draft creation on retry.

## Verification
- Automated in `backend/tests/test_chaos_and_resilience.py::test_chaos_02_worker_crash_mid_entry_atomic_rollback`.
