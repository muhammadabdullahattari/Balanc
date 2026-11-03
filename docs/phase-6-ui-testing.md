# Phase 6 - Independent UI testing

Date: 2 November 2026.
Status: **Completed for the approved local pilot UI-testing scope**, with the four user-approved manual/browser-matrix checks deferred to Phase 10.

## Implemented coverage

- `frontend/tests/phase6.test.tsx`: loading and empty recovery, labeled selection, pagination boundaries, semantic tables/exact server values, HTTP 401/403/409/422/429/503 recovery, missing membership, explicitly disabled privileged controls, offline/timeout normalization.
- `frontend/tests/phase6-screens.test.tsx`: actual Auth registration/verification/reset navigation and empty-submit validation; Accounts pending/empty/populated/failure/retry and classification-dialog dismissal; Dashboard selection, authoritative totals, chart exact-data alternative and reporting context.
- Existing UI tests cover exact money formatting, pending commands, permission controls, modal title/Escape and structural sign-in axe checks. Existing transport tests cover CSRF, refresh, write replay prevention and timeouts.

Isolated API doubles live only in tests. Production behavior and architecture are unchanged. CI already executes these files through `npm run test:ui`. Synthetic fixtures are not production books. jsdom cannot certify visual layout, contrast or native browser behavior.

## Verification results

- Locked dependencies restored successfully with npm ci.
- UI: 25/25 passed across four files on the final functional test source.
- Transport: 6/6 passed, with typed API contract compilation.
- Generated OpenAPI types check: passed.
- TypeScript and production Vite build: passed. Rollup stripped two third-party Zod comment annotations; build completed successfully.
- Production source formatting and Git whitespace checks: passed; added tests formatted with Prettier.

No new production defect was found by these additions. Two test assumptions were corrected: registration password label includes guidance; dashboard lists active assets only.

## Browser acceptance delivered

`frontend/playwright.config.ts` starts a dedicated production-preview server on loopback port 4176, requires an unused port, and runs isolated Chromium contexts with one worker, no retries, retained failure traces/screenshots and JSON/HTML reports under ignored local artifacts. No development database is accessed. Test routes intercept API calls only inside the test process; the production client is unchanged. Typed identity/business/account fixtures follow generated contracts; synthetic report/list/error payloads exercise UI presentation, not accounting correctness.

Ten production-browser journeys passed: native account validation/classification/submission, sales/purchases filter query parameters, failed-read explicit recovery, expired-session refresh termination, permission-denial feedback, conflict without mutation replay, aborted-request recovery, pending-to-empty transition, actual 30-second request timeout, and exact dashboard/chart/context presentation. These are representative core journeys; they do not assert exhaustive permutations of every form or backend correctness.

CI installs the locked Playwright dependency and Chromium, type-checks the browser tests, executes them after the production build, and uploads evidence even on failure. Hosted CI has not been run remotely. `npm run test:phase6` executes the local phase gate after `npm ci` and `npx playwright install chromium`. Tests require no provider keys or paid services.

Final results: **10/10 Chromium journeys**, **25/25 UI tests**, **6/6 transport tests**, generated contract check, browser TypeScript check, production build, formatting, workflow validation and release fixture-exclusion check passed. The axe test exceeded its original five-second limit during competing local work; its bounded timeout is now 15 seconds, and Vitest uses one worker. The subsequent complete suite passed without removing assertions. Detailed raw browser evidence is in `tmp/phase6/browser-results.json` and `tmp/phase6/browser-report/`; sanitized evidence is in `docs/contracts/phase-6-results.json`.

## Remaining acceptance boundaries

The original automated browser-journey gap is resolved by the passing Chromium suite. The 17 real-service Phase 5 journeys remain historical evidence, not a fresh Phase 6 run. Real backend financial effects and deployed integration remain Phase 8/10 gates. No production capacity or comprehensive accessibility certification is claimed.

User-approved Phase 10 deferrals: phone/tablet/desktop visual review; contrast/keyboard/screen-reader behavior; native file-picker uploads; supported browser matrix. Phase 5 technical implementation is Completed; these checks remain unverified.

Existing unrelated backend document-worker edits were preserved. No commit, push or deployment.

Dependency audit: three moderate development-tool findings affect minimatch through @redocly/openapi-core/openapi-typescript (GHSA-g3hv-75wg-m249). No high/critical findings were reported. npm proposed a breaking generator downgrade; it was not applied. Keep this tracked for a compatible upstream fix.
