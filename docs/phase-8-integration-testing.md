# Phase 8 - Independent integrated acceptance

Date: 2 November 2026.
Status: **Completed for the approved local P1 pilot integration scope.**

## Scope and implementation

The approved local P1 pilot remains the release scope. Tests use synthetic users, independent accountant/owner sessions and uniquely named disposable PostgreSQL databases, migrations through 0020, real SMTP, real ClamAV and the configured local OCR container. Development books are not used. Full SRS gaps and the four manual browser checks deferred to Phase 10 remain open.

Added a separate production Chromium integration runner on loopback port 4178. Unlike the Phase 6 isolated API doubles, this runner routes the production frontend through the real local API and creates its own restricted database. The browser signs in using real cookie/MFA recovery flows, deliberately approves a $100 invoice and $40 receipt, then independently checks $60 receivable, $40 cash and $100 income with exact integer millionths and a balanced trial balance. It checks duplicate command replay, stale command rejection and one financial effect. Tenant denial uses an actual unshared business and original evidence object plus jobs/search/exports. Browser session cookies are checked for Secure/HttpOnly.

A dedicated Vite preview proxy forwards /api without rewriting Origin. The isolated API allowlist explicitly includes port 4178. Test fixtures keep synthetic credentials in ignored local files, clean them up, and drop only their uniquely created database. Failure screenshots are ignored artifacts; tracing is disabled for the live fixture. The production application ships no mock financial data or new identity/storage authority.

The fixture now includes an unshared owner business, a private account and a quarantined original. This provides a real resource target for tenant-isolation checks; it is not simply an unknown UUID. Originals are cleaned after the fixture exits.

## Independent backend/security/recovery evidence

Fresh full all-services suite: **114 passed in 511.09 seconds; zero skips**. Raw JUnit: tmp/phase8/backend-results.xml; local log: tmp/phase8/backend-run.log. This reruns existing independent assertions rather than copying Phase 4 results.

- Accounting: invoice/partial settlement, cash/accrual timing, loan principal versus interest, fee/tax separation, refunds/advances, transfers and match reclassification tie to exact ledger balances.
- Durability: repeat migrations and populated prior-schema upgrade preserve data; synthetic backup restore compares rows/grants/RLS and financial reads.
- Security: forced PostgreSQL RLS, owner posting denial, revocation, password/session races, CSRF/origin limits, bounded request/errors and no public support bypass.
- Concurrency: duplicate posting has one effect, stale versions reject and failing mutations do not leave partial journals.
- Documents/jobs: unauthenticated body rejection, scanner-outage quarantine, exhausted retry/replay rules, real scan/OCR without automatic posting, retained signed export integrity and privacy review separation.
- Recovery: unavailable OCR/scanning preserves manual/retry boundaries; no paid/bank/money-execution/tax-filing provider is enabled or certified.

Relevant requirement groups: FR-001-004 identity/tenancy/policy/ledger; FR-005-007 trade/subledgers; FR-008/010 bank review; FR-009 evidence; FR-011/012 loans/transfers; FR-013/014 reconciliation/report/tax support; applicable SEC/VAL/NFR controls. The phase4-traceability.json registry provides detailed requirement references; passing local integration does not close its documented full-SRS gaps.

## Reproduce

Start the configured local Docker database, Mailpit, ClamAV and OCR services. From backend, enable RUN_PHASE2_INTEGRATION, RUN_DB_BOOTSTRAP_TESTS, RUN_DOCUMENT_SCANNER_TESTS and RUN_LOCAL_OCR_TESTS to 1; run `.venv/Scripts/python.exe -m pytest tests -q --junitxml=../tmp/phase8/backend-results.xml` (use the platform's backend Python path).

From frontend: `npm ci`, `npx playwright install chromium`, then `npm run test:phase8`. It includes contract inventory, API/type/unit/build/UI-browser checks, the real browser fixture, existing real HTTP service journeys and formatting. Port 8000 must be unused for each fixture; preview ports 4176/4178 must be free. On Linux set LEDGERPILOT_TEST_PYTHON to the backend environment Python. Run sequentially; do not disable rate limits or overwrite development database settings.

CI adds the production-browser/API/database gate beside the existing real-service/backend suite and preserves its JSON evidence. Hosted CI has not been run remotely. `scripts/record-phase8-candidate.mjs` records a deterministic SHA-256 source/config/test manifest with normalized LF text. This identifies a tested workspace candidate; it is not an image digest, signed release or deployed commit.

No commit, push or deployment. Existing unrelated backend docstring edits are preserved.

## Defects and harness corrections found

The real browser exposed unstable implicit select labeling in the shared Field component. Controls now reference the label span explicitly with aria-labelledby; guidance uses aria-describedby. A new component regression verifies the exact select name before and after selection and the separate description. All 27 component/accessibility tests passed after this fix; it changes form semantics, not posted financial logic.

Test corrections: identically named synthetic businesses are selected using their actual listed ID/order; test replay requests include the same required Origin/CSRF headers as the browser; real-browser artifacts now have a dedicated directory so they cannot collide with the UI-only browser suite. Failed intermediate runs are not counted as passing acceptance. The final real-browser journey passed without weakening accounting/security assertions.

Fresh browser proof: one multi-step Chromium journey passed against real HTTP API and PostgreSQL, including Secure/HttpOnly cookies, deliberate posting, $100 invoice/$40 receipt/$60 outstanding, exact bank/AR/income trial tie-out, one effect after replay, 409 stale rejection and 404 denials for an actual unshared tenant including its private evidence file, jobs/search/export APIs. No API routes were mocked in this suite. The ten UI-only Chromium journeys are a separate suite with isolated API doubles.

## Final exit evidence

All final gates passed: **114 backend tests with zero skips**, **18 real HTTP frontend/service checks**, **one multi-step real Chromium/API/PostgreSQL financial/isolation journey**, **27 component/accessibility checks**, **10 isolated-API Chromium regressions**, and **seven transport tests**. Production build, generated contract/browser types, Ruff, strict mypy (90 source files), inventory validation, formatting, workflow/CI gate validation, fixture-exclusion and Git whitespace checks passed. Offline uv lock/environment verification reported no changes. Synthetic credential files were removed and fixture teardown completed. The frontend and backend test environments were freshly isolated databases; this was not a fresh-machine/hosted deployment test.

See [sanitized results](contracts/phase-8-results.json) and [source candidate manifest](contracts/phase-8-candidate.json). The candidate fingerprints 207 source/config/test inputs, including the preserved pre-existing backend docstring edits. It is an uncommitted local workspace candidate; a deployed container/commit identity must be recorded in Phase 9.

Security review found no unresolved critical/high issue in these exercised local-pilot paths. This is not a penetration-test or whole-SRS certification. Three existing moderate development-generator findings remain tracked from Phase 6. Partner connectors, money movement and tax filing are disabled/out of scope; none is certified by these results. Hosted CI execution, deployment TLS/CORS/cookie behavior, the four manual browser deferrals and full-SRS gaps remain explicit later gates. **Phase 9 deployment is next.** Nothing was committed, pushed or deployed.
