# Phase 4 backend verification record

30 October 2026. Status: **complete for the approved local pilot**, following the user's explicit selection to verify that profile and record detailed SRS gaps. See ADR-014. The original SRS remains authoritative for broader product and production commitments.

## Delivered

- Reproducible service-independent OpenAPI export/check: `backend/src/ledgerpilot/export_openapi.py`. The reviewed artifact remains 121 paths and 229 schemas; its JSON content is unchanged from the previously saved contract.
- Generated browser request/response types in `frontend/src/api/generated/schema.ts`, produced by the locked `openapi-typescript` dependency. Do not hand-edit this file.
- One typed browser transport in `frontend/src/api/client.ts`, using `openapi-fetch`, cookie credentials and CSRF initialization. It invalidates the cached CSRF value after authentication changes or authorization errors, blocks cross-origin HTTP, and never automatically retries financial writes. Native request cancellation and typed error envelopes remain available. Local development must use the same-origin proxy or an explicitly configured HTTPS API. UI wiring remains Phase 7.
- Type compilation checks for valid routes/parameters and rejected invalid routes, pagination types and missing business identifiers. Four isolated transport tests exercise cookies, CSRF rotation/initialization failure, failed writes without retry and cross-origin transport restrictions. These test doubles stay outside the application bundle.
- Local/test Swagger request interceptor using the ordinary CSRF protocol. Interactive documentation remains absent in demo and production profiles. A real browser check found and corrected undefined HTTP methods on Swagger's schema request; GET is now the default. The configured API prefix determines the CSRF endpoint.
- Real PostgreSQL tests for a populated 0019-to-0020 upgrade, repeated head upgrade, restored permissions/RLS and independent financial totals. No migration or financial schema change was introduced by this phase.
- A machine-readable registry for all 44 FR/UC pairs and 60 VAL/NFR/SEC requirements, including corresponding TC identifiers, expected outcomes, routes, actual test pointers and limitations: `docs/contracts/phase-4-traceability.json`. Its source SRS SHA-256 and evidence references are checked by `ledgerpilot.verify_traceability`. A valid pointer means the test exists, not that it proves every clause.
- CI now detects backend contract drift and stale generated browser types, checks traceability references, runs browser transport/type tests and saves full real-service pytest/coverage artifacts. Hosted results require a new GitHub run after the user commits/pushes.

## Verification evidence

The full real-service run passed **112 tests in 777.88 seconds** with all four service flags enabled. It covered the existing unit/property, API, accounting, restricted PostgreSQL, SMTP, scanner and OCR checks plus the first two Phase 4 tests. This run started before the final Swagger changes and the additional migration/loan assertions.

The subsequent final Phase 4 focused run passed **4 tests in 39.74 seconds**. It covers the populated upgrade, synthetic backup/restore, exact loan totals and Swagger/security contract. The later configured-prefix documentation adjustment is separately rechecked; do not mislabel the earlier full run as one fresh 114-test run of the final tree. This separates actual results from collected/planned cases.

Ruff and strict mypy passed for 90 application files. Generated contract checks, four browser transport tests, positive/negative type assertions and the production web scaffold build passed. The final configured-prefix Swagger check passed one test in 11.85 seconds (three deselected). The API image `ledgerpilot-api:phase4` built and passed a real HTTP smoke check as UID 10001: PostgreSQL readiness at 0020, 121 schema paths and Secure/HttpOnly CSRF cookie. The temporary smoke container was removed. Workflow YAML parsing and Git whitespace checks passed. Results are recorded in `docs/contracts/phase-4-results.json`. No hosted CI result, external penetration test or production capacity result is claimed.

Measured full-run coverage: **79.47% combined**, **83.95% statements**, **60.17% branches**. Identity branch coverage is **53/80 = 66.25%**; accounting branch coverage is **46/66 = 69.70%**. This is whole-backend instrumentation, not changed-line coverage. The SRS's 90% accounting/auth branch thresholds are **not met**. Publishing this evidence does not turn it into a passing SRS quality gate.

## Independent accounting and recovery checks

- $100 invoice, $40 receipt: restored document remains $60 due; bank debit $40, receivable debit $60, revenue credit $100; summed debit-minus-credit balances equal zero.
- $100 loan received and principal repaid with $10 interest: loan principal balance zero, bank net credit $10, interest expense debit $10, income zero. Principal never becomes profit or expense.
- Existing golden tests separately verify cash/accrual timing, bills, transfers, unknown deposits remaining unposted, tax remittance classification, statement sign-off, idempotency, rollback, period locks and cross-tenant denial. These are actual referenced tests in the registry, not assertions that every conceivable accounting policy is supported.
- Restore uses a custom-format `pg_dump` of an isolated synthetic database, restores to a separately generated database, compares counts and content hashes of **every app table**, verifies runtime role is not superuser/BYPASSRLS, checks default RLS denial, readiness and authorized report reads. Both source and target are separate from development data. Only the known disposable target is removed by the test's finalizer.
- This is a same-cluster logical database restore using pre-provisioned global roles. It does **not** prove external original-document storage, encryption/signing-key recovery, point-in-time recovery, infrastructure loss, regional failover or production RTO/RPO. Those require separate storage/key backup and deployment recovery drills.

## Swagger execution record

Chrome used `http://localhost:8014/docs` with a loopback-only API and a dedicated disposable synthetic database. Its configuration explicitly allowed that local Origin. No real business or paid provider was used.

1. `GET /health/ready` returned 200 and `status=ready` against PostgreSQL at 0020.
2. `POST /api/v1/auth/register` with an invalid synthetic email returned 400 `ERR_EMAIL`, after the interceptor successfully initialized CSRF. It did not fail at the CSRF layer.
3. A valid synthetic local registration returned 202 with the generic verification-mail response. SMTP used Mailpit.

Request IDs are saved in the results file. Interactive authenticated accounting workflows were exercised in real-service pytest; they were **not** all manually repeated in Swagger. This representative UI record must not be described as clicking every route. The temporary UI server was stopped using a graceful stop signal and its known test database was removed by the fixture.

## Open SRS differences and follow-up triggers

These stay open under the user's chosen local pilot scope. They are not silently implemented, waived for production or removed from the SRS.

- **VAL-006 / SEC-009:** SRS upload 10MiB/20pages/40MP versus local upload maximum 25MiB and bounded OCR 10pages/16MP. Supported extraction is English PDF/PNG/JPEG; other accepted originals need manual handling. The parser's local bridge does not enforce outbound network denial. Resolve the contract/limits and deny parser egress before hosted document processing.
- **VAL-009 / NFR-012:** Several lists use unsigned UUID/integer cursors and limits up to 200 versus the SRS's signed, filter-bound cursor/limit100. Local request budgets differ from the SRS's separate human read/write defaults. Resolve before claiming full contract parity or opening the public developer platform.
- **VAL-016/017:** Local progressive throttling has no full CAPTCHA/subnet defense; financial operation keys are body fields, not the exact SRS UUID idempotency header contract. Preserve local guarantees; resolve the public contract and abuse controls before external platform release.
- **FR-002/017 / NFR-015:** Multi-business authorization/listing is not an aggregate hundreds-client exception dashboard; saved-report numeric lookup is not general conversational financial analysis. General question understanding, calibrated forecasts and published versioned OCR/matching holdouts remain unverified. Review the portfolio UI/backend needs in Phase 5; evaluate model capabilities before promoting them.
- **NFR-014:** Coverage targets above remain open; no automatic threshold check is represented as passing. Improve meaningful accounting/auth failure-path tests before a full SRS quality claim.
- **FR-037 / NFR-018:** Local retained snapshots are bounded at 10MiB, not a 100MB asynchronous export system with progress/cancellation. Larger exports require a separately approved workflow.
- **NFR-006/010 and SEC-008/014:** No complete original-file/key restore, production KMS/PITR, legal retention enforcement or regional recovery evidence. Verify the selected deployment backup profile in Phases 9/10 and funded production recovery in Phase 12.
- **NFR-001/002/007/020:** No declared production RPS, millions of pages, backlog fairness, sustained load or 24-hour soak evidence. Pilot tests establish functional correctness only. Performance verification remains environment-specific; production scaling is Phase 12.
- Frontend accessibility/localization/browser CSP, external notifications, paid billing, connectors, advanced privacy execution, operational alerts and external security assurance retain their corresponding later gates. No user count, certification or production readiness is inferred.

## Repeatable commands

From `backend`: `uv run python -m ledgerpilot.export_openapi --check`, `uv run python -m ledgerpilot.verify_traceability`, `uv run ruff check src tests`, `uv run mypy`.

With PostgreSQL/Mailpit/ClamAV/OCR healthy, enable `RUN_PHASE2_INTEGRATION=1`, `RUN_DB_BOOTSTRAP_TESTS=1`, `RUN_DOCUMENT_SCANNER_TESTS=1`, `RUN_LOCAL_OCR_TESTS=1`, then run `uv run coverage run -m pytest tests -q` and `uv run coverage report`. Use only the local test environment; fixtures create their own databases. Recovery-specific checks are in `tests/test_phase4_verification.py`.

To intentionally update a reviewed API contract, run `uv run python -m ledgerpilot.export_openapi`, then from `frontend` run `npm run api:generate`, `npm run api:check`, `npm run test:api` and `npm run build`. Review the schema and generated changes together. Never run a generator merely to hide an unreviewed contract change.

## Exit disposition

Phase 4 is **complete for the approved local pilot profile** with the results above. Phase 5 may build the responsive UI against this internal contract. Detailed SRS compliance, its coverage targets and later production requirements remain open. Nothing was committed, pushed or deployed by this implementation task.
