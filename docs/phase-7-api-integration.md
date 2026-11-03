# Phase 7 - Frontend/API integration

Date: 2 November 2026.
Status: **Completed for the approved local pilot integration scope.**

## Delivery

Phase 5 already integrated the approved local-pilot screens. Phase 7 reviews that integration and closes contract/configuration gaps rather than introducing another API client or replacing the architecture.

- Generated OpenAPI client remains the sole API transport; cookies use credentials/include, CSRF initializes for writes, refresh is shared for expired reads, and financial writes are never automatically replayed.
- Verified all 117 production frontend call sites covering 89 distinct operations against OpenAPI. `frontend/scripts/check-integration.mjs` uses the TypeScript AST to reject unreviewed dynamic API paths and noncanonical direct fetch/axios calls, and detects inventory drift. It is a static check, not endpoint acceptance or a general security scanner.
- Identity/MFA, business selection/current membership, tenant-prefixed query keys, role-aware actions, cache invalidation, logout/expiry clearing and revoked-membership handling reuse the existing backend authority.
- Real API integration covers sales/bills/credits/payments/refunds, loan/advance movements, CSV bank review/rules, opening imports, evidence/review, reconciliation/close, reports/tax support/exports, contacts/accounts and settings/activity. No mock adapter ships in the application; isolated Phase 6 API doubles remain outside src.
- Evidence originals upload through the authenticated binary endpoint and remain quarantined until scanning. Authenticated downloads use cookies and tenant authorization; temporary object URLs are revoked. The local profile does not mint long-lived browser bearer download tokens or expose private storage credentials.
- Added a fail-fast API-origin guard: embedded credentials, non-root paths, query strings and fragments are rejected instead of silently stripped. Cross-origin HTTP remains rejected.
- Added X-Request-ID header fallback when an error body omits its reference, preserving support diagnostics.
- Added a real HTTP CORS preflight assertion for the exact permitted web origin and a rejected untrusted origin. Cross-origin browser cookie enforcement/third-party-cookie policy still requires Phase 10 hosted acceptance.

## Contract mismatch and repairs

The nine previous integration defects (bank-rule history, categorization payload, opening trial baseline, explicit payment confirmation, annotation history invalidation, original credit reference, currency choices, inactive mobile navigation and bounded/error presentation) are recorded in phase-5-responsive-frontend.md. This update adds the origin guard and gateway-reference fallback, with focused regressions.

The first fresh real-service run passed 14/17 journeys. Its three failures came from forms defaulting to 2 November while assertions queried the dated 30 October fixture. Tests now explicitly enter the fixture accounting/payment/refund dates; expected balances were not relaxed. The added CORS test brings the final suite to 18 journeys. Final verification passed all 18 journeys, including the unchanged accounting balance assertions.

## Reproduction and environment

From frontend: `npm ci`, `npm run integration:check`, then `npm run test:phase7`. The real-service runner requires local PostgreSQL, Mailpit and ClamAV, available backend development dependencies, and unused port 8000. It creates an isolated temporary database and synthetic memberships; it does not use the development books. On Linux set LEDGERPILOT_TEST_PYTHON to the backend Python executable. Raw reports retain the compatible tmp/phase5/live-results.json location. Sanitized Phase 7 results are recorded separately.

VITE_API_ORIGIN is public and must be an origin only. Empty uses same-origin /api with local Vite proxy. FRONTEND_ORIGINS on the backend must enumerate exact origins. Hosted cross-site cookies require Secure and COOKIE_SAMESITE=none; same-site custom domains are preferable where supported. Domain, TLS, CORS and actual browser cookie behavior are verified at deployment/Phase 10, not claimed by the local cookie-jar suite.

No new service, schema migration or accounting/auth architecture. Existing unrelated backend document-worker edits were preserved. No commit, push or deployment. The Phase 10 manual browser deferrals and full SRS gaps remain open.

## Final verification

- 18/18 real HTTP frontend/service checks passed in the isolated PostgreSQL fixture, including allowed/rejected CORS origins. Original and synthetic credential cleanup completed.
- 26/26 UI tests, 7/7 transport tests and 10/10 Chromium UI regressions passed.
- 117 call sites / 89 operations matched the generated contract inventory; API generation, browser types, production TypeScript/build, formatting, workflow gate validation, release fixture exclusion and Git whitespace checks passed.
- Evidence: [sanitized results](contracts/phase-7-results.json) and [API inventory](contracts/phase-7-api-inventory.json). Raw local results are ignored. CI runs inventory checking alongside the existing real-service acceptance gate; hosted results are not claimed.

Known limitations remain the approved full-SRS gaps, Phase 10 browser/hosted-cookie acceptance and the three moderate development-generator dependency findings recorded in Phase 6. Phase 8 independently verifies integrated permissions/isolation, accounting effects, failures and recovery from a clean environment; it is not closed by this phase. No new backend architecture or hosted configuration was introduced.
