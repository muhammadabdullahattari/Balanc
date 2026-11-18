# Frontend review coverage

Scope: local responsive web application. Browser tests use controlled API responses. The inventory records source inspection and does not establish that every state has been exercised with real data.

## Source inventory

| Source | Detected UI or implementation role |
|---|---|
| frontend/src/api/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/api/client.ts | Supporting module, styles, or declarations |
| frontend/src/api/generated/schema.ts | Supporting module, styles, or declarations |
| frontend/src/api/runtime.ts | Supporting module, styles, or declarations |
| frontend/src/App.tsx | loading, error feedback, status announcement, navigation, data access, lazy loading |
| frontend/src/components/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/components/ui.tsx | status announcement |
| frontend/src/features/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/features/activity.tsx | form, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/advances.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/audit-access.tsx | form, table, loading, error feedback, status announcement, data access |
| frontend/src/features/auth.tsx | form, loading, error feedback, status announcement, data access |
| frontend/src/features/banking.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/beneficiary-review.tsx | form, table, loading, error feedback, status announcement, data access |
| frontend/src/features/billing.tsx | loading, error feedback, status announcement, data access |
| frontend/src/features/cleanup.tsx | form, table, loading, error feedback, status announcement, data access |
| frontend/src/features/close-packages.tsx | table, loading, error feedback, status announcement, data access |
| frontend/src/features/close.tsx | form, dialog, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/context.tsx | Supporting module, styles, or declarations |
| frontend/src/features/controlled-posting.tsx | form, dialog, table, loading, error feedback, status announcement, data access |
| frontend/src/features/dashboard.tsx | loading, empty state, error feedback, data access |
| frontend/src/features/directory.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/drafts.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/email-operations.tsx | form, table, loading, error feedback, status announcement, data access |
| frontend/src/features/evidence.tsx | form, dialog, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/export-disposal.tsx | form, table, loading, error feedback, status announcement, data access |
| frontend/src/features/google-signin.tsx | form, error feedback, status announcement, data access |
| frontend/src/features/ledger.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/loans.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/lookup-more.tsx | loading, error feedback, data access |
| frontend/src/features/onboarding.tsx | form, dialog, loading, error feedback, data access |
| frontend/src/features/openings.tsx | form, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/prepaids.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/projects.tsx | form, dialog, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/quickbooks-import.tsx | table, loading, error feedback, status announcement, data access |
| frontend/src/features/reconciliation.tsx | form, dialog, loading, error feedback, status announcement, data access |
| frontend/src/features/refunds.tsx | form, dialog, table, loading, empty state, error feedback, data access |
| frontend/src/features/reports.tsx | form, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/retention.tsx | form, table, loading, error feedback, status announcement, data access |
| frontend/src/features/rules.tsx | form, dialog, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/features/settings.tsx | form, table, loading, error feedback, status announcement, data access, lazy loading |
| frontend/src/features/sign-in-verification.tsx | form, error feedback |
| frontend/src/features/trade.tsx | form, dialog, table, loading, empty state, error feedback, status announcement, data access |
| frontend/src/forms/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/hooks/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/main.tsx | Supporting module, styles, or declarations |
| frontend/src/routes/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/styles.css | Supporting module, styles, or declarations |
| frontend/src/test/.gitkeep | Supporting module, styles, or declarations |
| frontend/src/vite-env.d.ts | Supporting module, styles, or declarations |

## Manual acceptance

- Test registration, password reset, Google sign-in and MFA with an authorized staging account and real mail delivery.
- Navigate each workspace section with keyboard only. Check dialog focus return, tabs, data-table scrolling, file picker and date picker.
- With NVDA or VoiceOver, read a form validation error, a loading message, an empty table, a permission error, and an offline notice.
- On Safari/iOS, Firefox and Edge, check 200% zoom, portrait/landscape, reduced motion, light/dark themes and long financial values.
- Check billing, imports, workpaper approval and disposal on synthetic staging records with distinct roles.
- Measure cold navigation and interaction on a throttled mobile profile. Capture LCP, INP and CLS under representative usage.
- Establish approved visual snapshots for verified accounts in both themes before using screenshot diffs as release gates.

## Local verification, 18 November 2026

The browser suite uses a local production build and intercepted API responses. To repeat
the optional browser matrix on Windows, install the Playwright Firefox and WebKit
binaries and Microsoft Edge, then run from the frontend directory:

```powershell
npm run build
$env:LEDGERPILOT_BROWSER_MATRIX = "1"
npm run test:browser
```

The file-chooser regression opens the chooser, selects a synthetic file, cancels the
dialog and verifies the next upload starts empty. Reduced-motion mode now limits
repeating animations to one iteration. Dashboard tests attach populated screenshots
in both themes to the Chromium report for visual review.

The production entry bundle decreased from 123.26 to 114.07 kB gzip after splitting
workspace onboarding and audit recipient access and updating timeout handling.
A local mobile Lighthouse run on sign-in after splitting reported performance 72,
accessibility 100, LCP 4.55 s, total blocking time 479 ms and CLS 0. The pre-split
run reported 75, 100, 4.52 s, 383 ms and 0 respectively. These single runs used a
local static server and synthetic signed-out responses. They establish neither
a paint-time improvement nor production Core Web Vitals. INP requires interactions
and representative field measurements.

Open acceptance items include real Safari/iOS hardware, assistive-technology
sessions, representative staging performance, and live provider workflows.

Verification results: 57 component tests and 10 HTTP transport tests passed.
The production build, browser TypeScript check, source formatting and API
inventory check passed (193 call sites across 154 operations). Of 60 browser
cases across Chromium, Edge, Firefox and WebKit, 58 passed together. The
WebKit layout sweep passed on its own. A network trace showed the retry test
intercepting the dashboard account lookup before the accounts screen opened.
After synchronizing the test setup, both WebKit retry and deadline checks passed.
All 60 scenarios have passing evidence across runs; the complete matrix has
not been rerun since the final test setup adjustment.
