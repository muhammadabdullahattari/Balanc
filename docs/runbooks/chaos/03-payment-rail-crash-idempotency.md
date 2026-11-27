# Chaos Drill 03 — Crash Mid-Supplier-Payment Rail Execution (Double-Pay Prevention)

## Scenario & Fault Injected
- **Fault:** Simulated network drop / process crash **after** acquiring the supplier payment execution lease (`status = 'executing'`, `execution_lease_token` set) or after external rail settlement before ledger posting completes.
- **Expected Invariant:** Concurrent or retried `POST /p3/supplier-payments/{id}/execute` requests must **never** transmit a second payout to the supplier's bank/Stripe account.

## Observed Behavior
1. Before calling Stripe `Transfer.create`, `execute_supplier_payment()` acquires a `SELECT ... FOR UPDATE` row lock, rejects any payment already in `('executing', 'settled', 'settled_unposted')` with `409 ERR_ALREADY_SETTLED`, and persists `execution_lease_token` + `lease_expires_at`.
2. Stripe calls carry deterministic `idempotency_key=f"lp-payout-{payment_id}-{idempotency_key}"`.
3. If external settlement succeeds (`settled_via_live_stripe = True`) while ledger journal auto-posting encounters a period lock or transient failure, the payment transitions to `'settled_unposted'` rather than failing and inviting a duplicate retry.

## Verification
- Automated in `backend/tests/test_chaos_and_resilience.py::test_chaos_03_payment_execution_lease_blocks_double_payout`.
