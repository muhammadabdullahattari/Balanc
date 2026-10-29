# Phase 2 and Phase 3 implementation audit

Date: 29 October 2026. Scope: the local P1 backend, its architecture, completion records, database migrations, and real-service regression coverage. This is a development audit and repair record, not production certification or completion of Phase 4.

## Findings and repairs

1. **High — stale login after password reset.** Login could verify the old password, pause while a reset revoked existing sessions, then insert a new session that escaped revocation. Migration 0020 introduces a database-maintained password generation and records the verified generation on each session. Access and refresh both reject a mismatched generation. Existing session revocation, MFA, ten-minute access expiry and refresh rotation remain in place. A real overlapping-transaction test holds the pre-reset user state, executes the actual reset API and then issues the stale session; both protected access and refresh must fail. This is fail-closed invalidation, not a new identity provider.

2. **High — backdated advance over-allocation.** Advance application checked the document's balance on the application date but ignored later settlements. A fully paid invoice or bill could therefore acquire a negative balance when an earlier advance was applied. The service now checks both the dated and final outstanding balance while holding the existing business lock, matching the payment workflow. Regressions cover customer and supplier directions and verify that rejection leaves the business version and settled balance unchanged.

3. **High — reversal before original entry.** The API and posting gateway allowed a reversal dated before the original journal, creating a fictitious historical negative balance. The service now returns `ERR_REVERSAL_DATE`; migration 0020 also enforces chronology at the database boundary. Tests exercise the HTTP route, a direct restricted-role posting-gateway call and an allowed same-day reversal. Posted history is not rewritten. Existing anomalous historical records, if any, need accountant-reviewed forward correction; migration does not silently alter them.

4. **Medium — future close tasks blocked historical readiness.** Readiness counted every pending mandatory task regardless of its accounting period. It now joins the tenant-scoped period and excludes periods starting after the requested date. Pending work in the current and earlier periods still counts. Tests compare readiness before and during the future period. This remains a current readiness assessment at a financial cutoff, not a reconstruction of historical task states.

5. **Medium — malformed CSRF input could raise an internal error.** Python's constant-time string comparison rejects non-ASCII strings. Middleware now rejects non-ASCII cookie/header values before comparing them. The test uses an actual malformed HTTP header and verifies the controlled 403 response without a database connection. Session-bound CSRF and strict Origin checks are unchanged.

6. **Documentation drift.** The Phase 3 setup instructions still named migration 0018 although final closure used 0019. Setup, readiness and migration documentation now require 0020. Historical verification counts/revisions remain historical evidence, not silently rewritten claims.

## Architecture review

- **Module boundaries:** FastAPI remains a modular monolith. Shared trade workflows serve sales and purchases; bank imports are evidence and dispositions, not an independent ledger. The review did not require a new broker, database, cloud service or auth provider. Some workspace routing remains large; split it along existing module boundaries when changing those workflows, without duplicating services.
- **Financial integrity:** Exact decimal amounts, canonical SQL posting, immutable posted headers/lines, linked source operations, deferred source guards, idempotency and business-version locking remain intact. The new chronology check is additive. Source transactions still cannot be independently reversed to bypass their subledgers.
- **Tenant and identity isolation:** Runtime and migration roles remain separate. Transaction-local scope, verified memberships, forced RLS and composite scoped references remain the defense layers. Session generation adds protection for the reset/login interleaving without weakening revocation, MFA, Secure/HttpOnly cookies or CSRF.
- **Concurrency and consistency:** Financial writers serialize on the business row; report reads use the shared business lock. Authentication locks session families, and durable refresh replay revocation is preserved. Separate admission-counter connections avoid nested pool starvation. This favors correctness in the local profile; it is not measured high-throughput architecture.
- **Documents and background work:** Originals remain encrypted and quarantined until a positive scan. Parsing occurs in a bounded non-root OCR service without database/auth/encryption credentials. Leases, bounded attempts and scoped replay remain in place. OCR is proposal-only. The local worker is explicitly operator-invoked; managed scheduling/delivery and deployment network restrictions remain later gates.
- **API and reports:** Explicit response contracts and saved reports continue to support frontend consumers. No public API/SDK or frontend implementation was introduced. Supported cash policies, deterministic forecasts and manual P1 privacy fulfillment remain explicit boundaries; unsupported accounting cases fail rather than invent data.
- **Deployment:** Migration 0020 upgrades existing data without resetting volumes. Rollback of financial/security history remains a forward-repair operation. Application code and migration assets must be deployed together; readiness rejects the previous schema revision.

## Completion and remaining gates

The implementation inventory for Phases 2 and 3 is present for the approved **local P1** scope. The audit found correctness gaps despite the earlier passing suite; the additional tests are now part of CI's existing all-tests real-service job. Completion is subject to the verification record below and does not mean all possible bugs have been excluded.

Phase 4 still owns independent requirement-to-test review, representative Swagger execution, restore testing, expanded concurrency/failure testing and generated TypeScript client verification. Browser HTTPS/cookie behavior belongs to UI integration and deployed E2E gates. Production rollout still requires measured capacity, backups/PITR and restore evidence, centralized metrics/tracing/alerts, ingress limits, managed job delivery and isolated OCR network policy. Report/audit/rate-bucket retention, database disk budgets and query plans on larger datasets must be measured and assigned operational cleanup policies before shared commercial hosting; the existing local record/file/operation caps are not a total database-size bound. No ten-million-user, production SLA, paid-cloud or fresh hosted-CI claim is made.

## Verification

Before repairs, the three accounting/readiness regression cases failed against the real API exactly as predicted (3 failed in 42.81 seconds). After repairs, the focused suite including the password-reset race passed (4 passed in 52.04 seconds). Customer/supplier parametrization and the malformed-CSRF test were then added for final acceptance.

**Final acceptance: 110 passed in 572.40 seconds (9 minutes 32 seconds).** All four real-service flags were enabled: `RUN_PHASE2_INTEGRATION`, `RUN_DB_BOOTSTRAP_TESTS`, `RUN_DOCUMENT_SCANNER_TESTS` and `RUN_LOCAL_OCR_TESTS`. This includes the existing authorization, RLS, immutable posting, idempotency/concurrency, report, import, opening, banking, extraction and export regressions. Tests use isolated databases; the development volume was preserved.

Ruff passes for application/tests and the new migration; strict mypy passes for 87 application files. Python compilation and `uv lock --check` pass. The existing development database upgraded from 0019 to 0020. The rebuilt API image runs as UID 10001 and passed actual HTTP readiness against PostgreSQL, Secure CSRF-cookie and OpenAPI checks. The saved contract is unchanged (121 paths, 229 schemas). The temporary runtime secret file was removed. Git whitespace checks pass. Hosted GitHub Actions, browser testing, a new coverage measurement and production load/restore tests were not claimed or performed in this audit.

**Conclusion:** Phases 2 and 3 meet their documented local P1 implementation gates after these repairs. Phase 4 remains the next separate verification gate. No unresolved high-severity issue was identified in the reviewed paths; this bounded review and passing suite are not a guarantee that no other defect exists.

No secrets were exposed. No unrelated changes were intentionally introduced. Existing user changes were preserved. No commit or push was performed.
