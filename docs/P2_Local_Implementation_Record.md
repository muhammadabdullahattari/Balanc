# P2 local implementation record

The local preparation track follows the [next phases plan](Next_Phases_Implementation_Plan.md#what-to-build-now). The P1 source reference is commit `c28c360bc2e7d4f298ef2d0307ae040ed009b0a8` with the existing working-tree frontend and authentication changes applied. This identifies the starting tree. It is separate from hosted Phase 9 and Phase 10 acceptance evidence.

## L1 P1 baseline

The frontend production build, TypeScript check and 13 focused frontend tests passed. The service-backed backend suite passed 42 tests in 121.50 seconds with disposable PostgreSQL databases and Mailpit. It covered the Phase 2 integration, Phase 4 verification and identity hardening suites. Backend lint, formatting and strict type checking passed. Hosted browser and live Google consent checks remain pending.

## L2 First foundation slice

- Reused the PostgreSQL evidence job ID and lease lifecycle. The worker now logs elapsed processing time and the result code alongside the existing tenant and job IDs. A database failure after claim also carries the job ID. The structured logger accepts duration as a numeric field and continues to filter free-form and sensitive values.
- Extended the existing synthetic database restore drill to include a real uploaded, encrypted evidence original. The drill copies encrypted evidence from a quiet test workspace to a separate backup and restored location. It checks the restored hash, decryption, queued job association, tenant-visible evidence metadata and quarantine behavior alongside the ledger balances and row-level security checks.
- The extended drill passed as part of the 42-test local run, using the existing PostgreSQL container's `pg_dump` and `pg_restore`.
- This drill uses a synthetic encryption key and a local copy. Hosted recovery still needs a coordinated snapshot method, independent key recovery and agreed RTO and RPO. Record elapsed restore time, backup age and financial and evidence reconciliation in that exercise.

## L2 Rollout gate and worker restart

- The server-side rollout policy requires `P2_EMAIL_DELIVERY_ENABLED=true` and the verified business UUID in the JSON array `P2_EMAIL_DELIVERY_BUSINESS_IDS`. The switch defaults to false, the list defaults to empty, and the demo profile denies delivery. Reload the process after changing configuration. The rollout decision is separate from tenant authorization and recipient consent.
- The synthetic restore drill now opens independent restricted PostgreSQL sessions to simulate a worker restart. It checks that an active lease blocks a second claim, an expired lease is reclaimed under the same job ID with a new token, the old token cannot complete the job, and the current token records a retry only once.
- The settings and Phase 4 integration suites passed 30 tests, including the restore and lease drill on local PostgreSQL. Backend lint, formatting and strict type checking passed.

## L3 Local business email delivery

Migration `0023` adds opt-in workspace email preferences and a tenant-scoped PostgreSQL outbox. In Activity, an eligible member can opt in, queue a current-day review, and inspect their own delivery history. The review can queue two messages per business, member and business day. One summarizes overdue invoices and the other summarizes overdue invoices with missing evidence. The recipient is the verified workspace member who requested the review. Email contains counts and directs the recipient back to the authenticated workspace for details.

The existing business and role checks apply to the review. The queue and worker both check rollout and consent. The worker checks quiet hours, invoice payment and dispute state, and linked evidence at claim time. It commits the sending state before SMTP I/O and uses a stable message ID. Explicit transient rejection and connection failures can retry up to three attempts. A dropped connection with unknown outcome or an interrupted sending job is held as uncertain for review. Mailpit acceptance is recorded as sent.

Verification used disposable PostgreSQL and real local Mailpit. The 31-test settings, restore and delivery run passed. A separate run of the final email integration passed with delivery history, cross-member denial of history access, concurrent delivery, duplicate queue requests, interrupted sends and payment suppression. Fourteen focused UI tests, the production build, lint, strict typing and OpenAPI checks passed.

### Run locally

1. Apply migration `0023` with the existing Alembic migration role. Keep the API and worker pointed at the matching schema.
2. Start Mailpit with `docker compose -f docker-compose.auth.yml up -d`. Use loopback SMTP on port 1025 without authentication or TLS for this local profile.
3. In the ignored runtime configuration, set `P2_EMAIL_DELIVERY_ENABLED=true` and set `P2_EMAIL_DELIVERY_BUSINESS_IDS` to a JSON array containing the approved synthetic business UUID. Restart the API after editing settings.
4. Sign in with an eligible verified member. In Activity, enable workspace reminders, save preferences, then review and queue today's emails. The workspace needs overdue, undisputed invoices with an outstanding balance.
5. From `backend/`, run the following command for the verified member and business. Each invocation processes at most one job. Repeat for the next job. Add `--status` to list the latest delivery states without sending. Inspect the captured messages in Mailpit on port 8025.

```powershell
uv run python -m ledgerpilot.notifications.worker --actor MEMBER_UUID --business BUSINESS_UUID
```

## L4 Projects and billable expenses

Migration `0024` adds tenant-scoped customer projects, tags for posted income and expense lines, and billable allocations. Tagging adds project and optional location metadata to the existing ledger line. It leaves the journal amounts intact. The Projects page reports posted income, cost and margin from tagged lines and includes exact linked reversal lines in the requested date range.

A billable source is a tagged, undisputed supplier-bill expense debit without a reversal or credit. The local slice rebills the full source expense once. It allows separately entered sales tax, validates it through the invoice rules, and reserves the expense when preparing a draft. Posting still requires the existing invoice approval, version check and period controls. Voiding a draft releases its reservation. Issuing a draft marks the allocation as billed and tags the resulting income line within the same transaction. Billable drafts are edited by voiding and recreating them. Partial rebilling, tag correction and imported time are outside this slice.

To use the feature locally, migrate with the migration role, set `P2_PROJECT_BUSINESS_IDS` to a JSON array of approved synthetic business UUIDs and restart the API. Open **Projects**, create a customer project, select the expense date range, tag an eligible line and draft the invoice. Use **Draft review** to issue or void it. The project feature is off by default and available only in the local and test profiles.

Six service-backed PostgreSQL and Mailpit tests passed, including the project workflow, email delivery and restore regression. The project test covers default-off and cross-tenant access, a $50 posted expense, idempotent draft preparation, duplicate prevention, void and release, approval and atomic allocation, and posted project margin. The production web build, backend lint, strict type checks, and generated client checks passed.

## L4 Prepaid schedules and close review

Migration `0025` adds tenant-scoped prepaid schedules. In an approved local workspace, a preparer selects a posted manual-journal asset debit and a separate expense account. The schedule allocates the full debit across 2–36 month ends in exact currency units. A different member with posting permission approves each due installment in order. The approval uses the existing journal gateway and links the debit to expense and credit to prepaid asset to the schedule in the same transaction.

Close readiness includes outstanding prepaid installments. PostgreSQL rejects a period lock while it contains unposted installments, and prevents direct reversals of journals linked to a schedule. Schedule correction, cancellation, and scheduling against supplier bills remain outside this local slice. Independent close reviewers can add a separate note while preserving the original preparation evidence and comment.

For local use, apply migration `0025` with the migration role, add the approved workspace UUID to the JSON array `P2_PREPAID_BUSINESS_IDS` in the runtime configuration, and restart the API. The feature is off by default. Use **Prepaid expenses** to prepare or review installments and **Month-end close** to see the pending count.

Three focused service-backed prepaid and close tests passed after correcting the approval fixture to use a second accountant with posting rights. A separate run passed the populated migration and restore checks. The prepaid test verified a $100 schedule, exact minor-unit allocation, duplicate prevention, separation of duties, period lock, reversal protection and final ledger balances. Fifteen focused frontend tests, type checks, lint and OpenAPI verification passed.

## L4 Statement reconciliation preview

The local Reconciliation page previews a selected account and statement date against posted ledger entries, matched and outstanding items, and imported bank rows still in review. It shows the unexplained difference before approval. Preview creates no statement. Sign-off repeats the same checks inside the existing authorized, versioned command. Use `P2_RECONCILIATION_BUSINESS_IDS` to enable the preview for approved synthetic workspaces.

## L5 Cash scenarios and cited variance

In Reports, select the due-date baseline or the stress scenario with 75% of expected receipts and 125% of expected payments. The forecast API also accepts integer receipt and payment percentages from 0 to 200. It preserves opening cash, dates and source document references, scales weekly receipts and payments in exact decimal units, and displays the first week ending below zero. Each scenario is stored with its assumptions in the existing report history. There is no confidence band without calibration evidence.

Profit and loss can compare the selected period with the immediately preceding period of the same length. The comparison reads two saved reports from the authorized workspace, rejects mismatched report settings or overlapping periods, and identifies up to five account contributions by absolute impact. The screen cites both report IDs. The amounts describe changes in the books and do not infer business causes.

Enable these local features with `P2_INSIGHT_BUSINESS_IDS`. Both new settings use JSON arrays of business UUIDs, default to empty, and require an API restart. No new migration is required for this slice.

## Verification of statement preview and report insights

Two focused tests passed using disposable PostgreSQL, Mailpit and ClamAV. They covered the cash scenario, report history, comparison validation, preview availability, reconciliation mismatch, final sign-off and signed evidence export. The full frontend component suite passed all 46 tests using one worker. The production web build, TypeScript, backend source and test lint, strict backend typing, OpenAPI, generated client, and integration inventory passed. Broad lint across Alembic history still reports pre-existing line-length violations in older migrations.

## L4 Nightly close workpaper

Nightly close captures the previous calendar date in the workspace timezone. It runs the existing close checks against one PostgreSQL snapshot and stores the workpaper, its control reports and the audit event in one transaction. Each workspace has one immutable capture per date. Repeating the command for that date returns the original report ID. The dated workpaper shows bank rows in review, required tasks, prepaid installments, and accounting and tax control tie-outs. The capture timestamp distinguishes the workpaper from later ledger and review activity.

Set `P2_MICRO_CLOSE_BUSINESS_IDS` to a JSON array of approved workspace UUIDs and restart the local API. Run the following from `backend` with a verified active owner or accountant as the actor. The command exits with a failure status if the capture fails. The Close page displays the saved result under **Nightly close workpaper**.

```powershell
.\.venv\Scripts\python.exe -m ledgerpilot.workspace.micro_close --actor USER_UUID --business BUSINESS_UUID
```

The command captures the previous workspace date each time it runs. Add `--watch` to check once a minute and capture each completed workspace day while the local process runs. It uses the workspace timezone and reuses existing captures after restart. It captures the latest completed date after downtime. Daily capture is observational and leaves approval and period locking to the existing close workflow. Document completeness and external source freshness still require evidence.

The PostgreSQL and Mailpit integration run passed both tests. It verified the timezone cutoff, concurrent capture, one audit event, access to the cited reports, and immutable results after a late bank import. The forecast and variance regression passed in the same run. The frontend build, API contract checks, Ruff and mypy passed. The full frontend suite passed 45 of 46 tests on its first run. The evidence refresh test timed out waiting for the initial list in that run and passed when rerun on its own.

## L4 Onboarding parallel-run comparison

Owners and accountants can select a financial import from **Opening balances**, choose a comparison date on or after cutover, and paste legacy account codes and signed debit-minus-credit balances from two spreadsheet columns. LedgerPilot compares the supplied balances with the posted general ledger as of that date, reports exact differences per account, and runs the existing receivable, payable and loan control checks. The submitted trial balance must net to zero. Omitted accounts are treated as zero. The comparison leaves journal and import records intact.

The original cutover comparison continues to use the baseline saved on the import. The parallel-run comparison uses the newly supplied source balances and supports up to 1,000 supplied account codes.

The PostgreSQL and Mailpit integration test passed for both comparison routes. It verified exact differences, control reconciliation, and rejection of an unbalanced source trial or a date before cutover. Eleven focused frontend component tests passed, including the spreadsheet paste workflow. The frontend build, API contract checks, Ruff and mypy passed.

## P2-B Workspace email operations and delegated support

Migration `0026` adds immutable bounce and complaint feedback and revocable diagnostics grants. The local email rollout settings from L3 also enable **Workspace email operations** in Activity. Owners and accountants with MFA can inspect counts and recent job outcomes, record confirmed delivery feedback against a job ID, and grant an existing workspace reviewer up to 24 hours of read-only email diagnostics. Grant creation, delegated reads, revocation and feedback are audited. The API and PostgreSQL enforce the business scope. Delegated diagnostics exclude recipient identities and email contents.

Confirmed feedback suppresses future workspace email for that recipient in the same workspace. New jobs are skipped and queued jobs are suppressed when the worker claims them. The same feedback on the same job is recorded once. A send already claimed by the worker may be in flight. SMTP acceptance and later feedback are stored independently.

The local workflow uses Mailpit and manual recording of confirmed feedback. Hosted sender setup and automatic provider feedback ingestion require a selected provider and sandbox verification.

The PostgreSQL and Mailpit integration and populated upgrade tests passed. The integration covered duplicate feedback, complaint and bounce suppression, redacted reviewer diagnostics, denial before granting and after revocation or expiry, and persisted audit events. Twelve focused frontend tests, backend lint and typing, and the generated OpenAPI checks passed.

## Local nightly close details, cited forecast answers and approved privacy exports

The nightly workpaper shows saved ledger and source balances, differences, and control report references for receivables, payables, loans, and tax. A local watcher runs the existing immutable capture and retries temporary database failures.

In Reports, approved local workspaces can ask for the first projected shortfall and projected closing cash for a saved forecast. Both answers cite the selected report. A forecast with no shortfall returns an empty date and the UI explains the result. Answers use the saved data and execute no actions.

Privacy settings offer an MFA-protected personal data download. For an approved export request, the server checks the requester and current approval before generating the attachment, then records the request ID in the audit log. The existing general self-export stays available. The attached data uses the existing account and workspace privacy scope.

Three PostgreSQL and Mailpit integration tests passed. They covered the watcher crossing a workspace day boundary, source citations and no-shortfall answers, and requester-only approved exports with an audit event. Twelve focused frontend tests passed, including the cited shortfall answer.

## P2-F/H Local forecast and bank-rule evaluation

Reports can compare a saved baseline cash forecast with posted cash on its selected accounts. The evaluator accepts baselines captured on their forecast date and measures only completed weeks in the workspace timezone. It reports weekly differences and mean absolute error using decimal balances. The ledger can change after a week closes through later postings. The evaluation therefore describes posted balances at the time of review. Scenarios and older reports without saved cash account IDs are excluded.

Bank rule review can compare a saved rule version with previously categorized entries in a date range and under an explicit amount cap. It reports agreements and the bank row IDs that disagree. The sample excludes splits, reversed or corrected entries, and entries outside the rule's income or expense direction. The result evaluates this rule on the selected history. Representative holdout testing and accounting approval remain separate.

The PostgreSQL integration test verified a completed forecast week with a $42 difference and a shadow run with one agreement and one disagreement. It also verified the amount cap and default-off access to the rule evaluation.

## Next implementation checkpoints

- **P2-B:** Hosted sender and automatic provider feedback ingestion and verification.
- **P2-C/D:** Stripe subscription entitlements and event reconciliation, Financial Connections consent and feed ingestion, and QuickBooks Online direct synchronization. CSV remains available. Local provider contract work and sandbox acceptance remain open.
- **P2-E:** Scoped, revocable sharing of saved workpapers is implemented below. Workpapers beyond the nightly control checks remain open.
- **P2-F/H:** Scheduled inbox shortfall notifications and historical-error interval evaluation are implemented below. Representative holdout results for controlled posting and a versioned quality/cost benchmark remain open. No customer-quality claim follows from synthetic tests.
- **P2-G:** Configurable record-class retention and legal holds are implemented below. Automated disposal requires the approved record/copy/backup policy matrix and remains open.

P2-C, P2-D and P2-G now have recorded launch choices in the [P2 plan](P2_Implementation_Plan.md#launch-and-provider-decisions). Plan acceptance first for a US cohort, then expand globally market by market. Use Stripe for subscriptions and Stripe Financial Connections for live bank data. Keep CSV as the first available import and fallback, with QuickBooks Online as the first direct accounting connector. Use ten years as the default for in-scope financial records, with approved policy rules and exceptions. Provider eligibility, sandbox credentials and market retention review remain open. Hosted gates remain deferred.

## 13 November 2026 · Retention controls, cash alerts and forecast intervals

### Retention and legal holds

Migration `0027` adds immutable policy versions and durable category-wide or workspace-wide legal holds. Owner/accountant MFA sessions can record a cited policy, place a hold and release it with a recorded reason. Concurrent policy edits use an expected version; a repeated release preserves the first release evidence. PostgreSQL enforces tenant scope, allowed write columns, policy immutability and one-way release. Retention operations remain available after trial expiry, subject to the same role, MFA and rollout checks.

Set `P2_RETENTION_BUSINESS_IDS` to a JSON array of approved local/test workspace IDs and apply migrations with the migration role. Settings → Privacy displays the policies, hold history, policy form, hold/release controls and date-based eligibility preview. The default is ten years from the supplied record date. A February 29 anniversary falls on March 1 in a non-leap year. Any active matching hold blocks disposal review. This does not implement physical deletion, backup expiry or legal compliance certification; the response always states `deletion_authorized=false`.

### Scheduled cash-shortfall inbox alerts

The local worker checks the current workspace-day baseline for explicitly selected cash accounts. It requires an active, verified owner/accountant and the existing `P2_INSIGHT_BUSINESS_IDS` rollout. It respects the member's reminder preference and quiet hours. It stores the forecast and inbox notification atomically, citing the saved report, and deduplicates per member/day/account selection under the business lock. Activity offers a source viewer showing the saved projected shortfall and report date. Alerts are in-app, not new SMTP deliveries.

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m ledgerpilot.notifications.shortfalls --actor USER_UUID --business BUSINESS_UUID --cash-account ACCOUNT_UUID --watch
```

Repeat `--cash-account` for additional cash accounts (maximum 20). `--watch` checks every five minutes while the process runs; omit it for one check. Downtime does not create historical notifications. A same-day alert is an immutable snapshot, not a continuously updated cash balance. No background service is automatically installed.

### Historical-error uncertainty intervals

Reports → Compare a saved forecast offers an uncertainty evaluation for weeks 1–13. The evaluator accepts a contemporaneously captured baseline, uses only earlier baselines with matching currency, timezone and cash accounts, and excludes overlapping forecast windows. It uses 20 chronological calibration samples and 10 later validation samples; the absolute-error radius uses the finite-sample 80% quantile. Validation coverage below 80% yields no interval. Insufficient history also yields no interval. All source report IDs, method version and counts are returned. Source reads are bounded at 500 reports, and actual cash is calculated in three bounded grouped queries, preserving the shared 14-checkpoint limit. Actuals can include subsequent accounting corrections, so this is historical validation rather than a guarantee of future coverage or a frozen external benchmark.

### Verification status

The initial individual PostgreSQL/Mailpit tests passed for retention and concurrent cash alerts. After restarting Docker, the combined run exposed the existing 14-checkpoint query limit; calibration was corrected to three bounded grouped reads. The final retention/alerts/forecast run passed five tests, including synthetic temporal holdouts through the real API and database. The broader backend suite passed 76 tests with 104 service-dependent tests skipped; those skips are not integration evidence. Frontend production build, 50 component tests, nine transport tests, API client generation and integration inventory passed before the subsequent audit-sharing addition. Final audit-sharing checks follow below. No deployment, commit or push was performed. P2 is still open for the local work listed above, including subscriptions and direct connectors.

### Scoped auditor workpaper sharing

Migration `0028` adds recipient-specific, expiring grants for 1–20 selected immutable saved reports. Set `P2_AUDIT_BUSINESS_IDS` for approved local/test businesses. Under Settings → Privacy, an owner/accountant with MFA records the recipient's LedgerPilot account UUID, saved report UUIDs, a reason and a 1–720-hour duration. Share the displayed recipient link through your own authorized channel. The recipient opens `/audit-access`, signs in with MFA and opens/downloads only the selected workpaper. That page also displays their account reference to give to the owner. No email is sent by the share action.

The recipient does not need workspace membership. PostgreSQL limits the non-member to report rows named in active grants. Revocation and expiry are checked at each read, and reads are written to the workspace audit trail. Report links inside a shared snapshot do not confer access to the linked documents. Existing independent memberships or other grants are unaffected; downloaded copies cannot be recalled. Revocation remains available after trial expiry. Grant history uses bounded pagination, and source report scope is checked both by the API and the database trigger.

### Final local verification for this increment

Ten tests passed in the combined real PostgreSQL/Mailpit run covering retention, cash-alert concurrency, forecast evaluation/calibration, scoped auditor access, populated upgrade and isolated database/evidence restore. Auditor tests verify no workspace membership, database row filtering, wrong-recipient denial, audit recording, expiry and revocation. Readiness and upgrade/restore expectations now require schema `0028`.

All 51 frontend component tests passed. The production bundle, generated API types, 154-path OpenAPI check, 166-call-site/129-operation frontend inventory, Ruff and strict mypy passed. Nine API transport tests passed earlier in this increment. The general backend run passed 76 tests with 104 service-dependent skips before audit sharing; use the focused real-service evidence above for the added workflows. These are local checks, not provider sandbox, browser-manual or deployment sign-off. Migrations were exercised on disposable databases; rollout flags remain opt-in and were not changed in the user's private `.env`.

Pending product input: subscription plan names/prices/currency, per-plan limits and failed-payment grace rules. Remaining local implementation is still recorded under Next implementation checkpoints; billing and connectors are not implemented by this increment.

## P2 sandbox endpoints

Apply migration 0029 before running the backend. The Stripe dashboard endpoint is the
public HTTPS API origin followed by /api/v1/billing/stripe/webhook. Put its signing secret
in STRIPE_WEBHOOK_SECRET. Set BILLING_RETURN_URL to the HTTPS application return page.
For Intuit, register the public HTTPS API origin followed by
/api/v1/integrations/quickbooks/callback and use the identical value in QUICKBOOKS_REDIRECT_URI.
Start the flow as a signed-in workspace owner from the same API origin.

Checkout, signed event receipt, owner-triggered billing reconciliation, QuickBooks OAuth,
token refresh and paginated sandbox preview are available. The event inbox does not yet
trigger reconciliation automatically. Entitlement enforcement, Stripe plan changes and
the reviewed QuickBooks ledger import worker remain open.
