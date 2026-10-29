# ADR 012 — Accountant-reviewed opening balances

Date: 29 October 2026. Status: accepted for local P1 following the user's explicit selection of accountant-reviewed opening balances.

## Decision

Extend the existing reviewed CSV import pipeline with `mode=opening`. Preserve the modular monolith, canonical posting, PostgreSQL financial authority, forced tenant RLS, immutable sources and operation idempotency. A separate accounting engine or authentication provider is not introduced.

An accountant with MFA approves an explicitly selected active equity account, cutover date, review reason, supplied signed trial baseline and each outstanding customer/supplier/loan balance. Owners and bookkeepers may prepare/preview but cannot approve opening imports through their role alone. The import is atomic, limited to 25 source items, allowed once per business and requires no existing posted journals. Existing chart, parties and open fiscal periods may already exist.

Post only the outstanding amount on the cutover date against opening equity. Do not recreate historic sales, expenses, loan cash receipts, tax components or partial-payment history. Trade sources retain their original date in `opening_source_date`; their accounting date is cutover and their original due date may already be overdue. Loan principal uses a distinct `opening` movement, not a drawdown. Immutable opening metadata retains original/due dates, source reference, reviewer, reason and equity account. Each source is linked to its own canonical posting operation and reviewed metadata by deferred database guards.

Existing allocations, aging, party statements, control reports and principal repayments consume these sources. Before cutover, neither their GL nor operational balance exists in this system. Imported amounts represent residual balances, not original face values. Opening principal excludes interest and fees; no unsupported loan schedule is invented.

## Reporting boundary

Superseded in part by [ADR-013](ADR-013-local-p1-closure.md): evidence-backed immutable opening cash policies and linked source corrections are now implemented. The default remains abstention until a supported policy is explicitly approved.

Cutover does not recognize income or expenses on accrual reports. Original cash-recognition categories, tax history and prior cash-basis treatment are unknown from residual balances alone. Cash-basis financial reports therefore return `ERR_CASH_POLICY` for scopes containing opening trade balances. They do not silently recognize equity as revenue or expense. Accrual reports, settlement, aging, GL, control reports and exports remain available. Supporting cash recognition requires a separately reviewed policy/history extension; a dashboard using cash financial reporting observes the same explicit error.

## Durability and correction

Preview and failed approval write no financial data. Any mismatch with the supplied trial baseline or source controls rolls back the entire batch. Exact-key retries return the original result; repeated files and subsequent opening attempts are rejected. Posted opening sources and their metadata remain immutable; they cannot be erased through import rollback or an independent GL reversal. Review preview and legacy trial totals before approval. Source-aware posted correction workflows remain subject to the broader Phase 3 closure review; no destructive reopening is provided.

The opening history is exposed through scoped `/opening-balances` and included in signed evidence exports. The schema upgrade is forward-only, revision 0018, and preserves existing financial data.

## Evidence

`tests/test_opening_balances.py` uses real PostgreSQL, SMTP, accountant sessions and the API. It covers all four opening kinds, equity offsets, overdue dates, pre-cutover zeros, ordinary later settlement, control tie-outs, exact-key retry, file deduplication, owner denial, invalid source input, atomic baseline failure, zero cutover P&L and explicit cash-policy rejection. Final full-suite results are recorded in the Phase 3 implementation record.
