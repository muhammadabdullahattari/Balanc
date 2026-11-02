# LedgerPilot Phase 5 — Responsive web implementation and acceptance

Date: 30 October 2026.

**Functional implementation and real-service local acceptance: complete for the approved local pilot.**
**Overall phase sign-off: open because visual/browser acceptance could not be completed.**

This record supersedes the earlier incremental lists. The user explicitly brought real API integration into Phase 5, advancing part of Phase 7 without changing the FastAPI modular monolith, PostgreSQL financial source of truth or React/TypeScript/Vite architecture. The accepted Phase 4 local-pilot boundaries and documented SRS gaps remain in force. No native mobile app, money execution, tax filing, public SDK, paid service or production capacity claim is introduced.

## Delivered workflows

- Identity: registration, email verification, sign-in, MFA enrollment/recovery, password reset, cookie/CSRF transport, refresh rotation and session-cache clearing.
- Business context: selection, invitations, supported base currencies, accounting basis, fiscal start month, timezone and jurisdiction. Private pages carry noindex metadata.
- Navigation/design: grouped sidebar, responsive layouts, light/dark tokens, shared controls, reduced motion, keyboard focus, dialog semantics, mobile navigation focus containment and inactive navigation made inert. Offline state and report load timestamps are explicit.
- Dashboard: authoritative cash, receivables, payables, profit and trend figures with an exact-data alternative to the chart.
- Accounts/contacts: paged lists, creation, continued account/contact lookups and role-specific customer/supplier/lender/borrower statements.
- Sales/purchases: invoices, bills, immediate sales receipts, credit notes, tax lines, original-document linkage, server preview, draft saving and explicit posting. Separate document-kind filters expose receipts and credits after posting.
- Settlements/refunds: confirmed payment recording, explicit fees, original-payment and credit-note allocation, and retained command keys for manual retries. No financial write is automatically replayed.
- Drafts/journals: prepared document edit/issue/void, ledger source/approval drill-down and linked reversal controls.
- Loans/advances: borrowing/lending, optional repayment schedules, principal/interest/fees, deposit recording and document allocation.
- Banking: CSV mapping/preview/import, matching candidates, categories, splits, transfer/card-payment review, explicit clearing approval and authoritative accounting preview. Processed rows are not offered as new review items.
- Rules: inactive creation, affected-row preview and deliberate activation/deactivation.
- Migration: accountant-reviewed opening CSV, original source dates, opening equity, explicit legacy trial baseline, preview hash and atomic apply. A mismatch rolls back instead of inventing a balancing entry.
- Evidence: upload, scan/extraction status, original download, text, source fingerprints, field-region annotations, paged review history and financial-record linking.
- Reconciliation/close: statement evidence, outstanding entries, unexplained-difference checks, tasks, review state and explicit period lock/reopen confirmation.
- Reports: P&L, balance sheet, trial balance, AR/AP aging, deterministic forecast, tax workpaper, cited report lookup, journal CSV and evidence-pack export.
- Supplier safety: held bank-detail proposals, masked business change alerts and documented independent-verification boundary; no automatic beneficiary activation.
- Disputes: flag/resolve with a reason and version check, preserving posted document totals.
- Settings/activity: memberships/invitations, role-aware posting controls, notifications/preferences, privacy requests and session revocation.

## Defects found and repaired during real acceptance

1. A populated bank-rule list returned history rejected by its response contract. Added the allowlisted history field and regenerated OpenAPI/browser types.
2. Bank categorization sent a target account instead of the required single allocation, and an absent peer became the literal string `null`. Corrected action-specific payload construction; split amounts use positive allocation magnitudes.
3. Opening import always sent an empty legacy trial baseline, making financial import impossible. Added explicit baseline account/balance inputs and duplicate-code validation.
4. Payment recording posted immediately after form submission. Added a separate confirmation step without changing the retained idempotency key.
5. Evidence notes saved successfully but their history stayed stale. Added query invalidation; changing documents resets review pagination/status.
6. Credit-note payloads used the wrong original-reference field. Corrected to the generated `original_id` contract.
7. Supported-currency choices included unsupported AED and omitted approved currencies. Aligned options with the backend currency registry and exposed fiscal start month.
8. Mobile closed navigation remained keyboard-focusable. Added inert/hidden handling, breakpoint tracking, focus containment and Escape dismissal.
9. Added precise validation-field feedback, safe network errors, a 30-second transport timeout with cancellation preserved, and offline/freshness presentation.

The current-membership API exposes only the authenticated caller's verified membership. PostgreSQL/FastAPI continues to enforce tenant scope and permissions; client controls are presentation only.

## Reproducible acceptance

Run from `frontend`:

```text
npm ci
npm run api:check
npm run test:ui
npm run test:api
npm run test:live
npm run build
npm run format:check
```

`test:live` requires the configured local PostgreSQL, Mailpit and ClamAV services and backend development dependencies. Stop any API already using port 8000 first. On Windows it uses `backend/.venv/Scripts/python.exe`; on Linux set `LEDGERPILOT_TEST_PYTHON` to the backend environment's Python executable.

The runner creates a disposable database, migrates to 0020, seeds synthetic independent owner/accountant memberships and two businesses, starts the real HTTP API and scoped scanning worker, then drives production React components with real HTTP/cookies. It removes fixture credentials and test originals and closes the disposable database on normal completion. No production network adapter or financial response is mocked. Requests are paced below unchanged tenant limits. CI runs these checks beside real-service backend verification and stores the JSON results.

jsdom's synthetic FileList does not update its internal required-file validity. Only those component-test submissions use a direct form submit after asserting the selected file; production required-file validation remains enabled. This is not browser file-picker acceptance.

## Evidence and boundaries

- **17/17 real-service frontend journeys passed**, covering identity, financial writes, refunds, loans, CSV review, rules, migration, document scanning/review, reconciliation, close and exports. See [results](contracts/phase-5-results.json).
- **6/6 component/accessibility-structure tests passed.** axe's color-contrast rule is disabled in jsdom because it has no browser layout engine; no contrast certification is claimed.
- **6/6 transport tests passed:** CSRF initialization/rotation, no financial write replay, safe initialization failure, HTTPS enforcement, shared refresh rotation and bounded request timeouts.
- **26 backend regression tests passed; 2 service-gated tests skipped** in the contract/configuration/health regression command. Real services are separately exercised by the 17 live frontend journeys.
- Real membership test previously passed, including owner posting denial and another-business 404. Ruff and configured mypy pass; OpenAPI has 122 paths and generated types are checked. The 104-requirement registry validates references, not full SRS acceptance.
- Production build and formatting checks pass. Local build success does not certify hosted deployment, production operations or capacity.

### Unresolved browser acceptance boundary

Real Chrome previously completed sign-in and business selection. During this final attempt, screenshot/control commands stalled. The Windows fallback then **stopped Computer Use because it could not determine the current browser URL confidently enough to enforce policy**. No further UI control was attempted after that stop.

Consequently screenshots, visual review at 320–2560px, browser contrast/screen-reader checks, the full supported-browser matrix and native file-picker journeys remain **unverified**. These are required before unconditional Phase 5/UI acceptance; they are not represented as passing. The React/HTTP suite is repeatable and passing, but does not replace a browser engine.

Nothing was committed, pushed or deployed. Phase 9 deployment and Phase 10 deployed system acceptance remain separate gates.

Final verification: all 17 real-service journeys passed again on the finished 30-second transport. Production build, formatting, generated types, configured mypy, Ruff, workflow YAML/live-CI-gate validation and whitespace checks passed. The web bundle contained no configured private values. Synthetic live credentials and the disposable fixture were cleaned up. Hosted CI was configured but not executed remotely.
