# LedgerPilot Implementation Plan

## Sequenced engineering delivery from project setup to production

**Plan version:** 2.12
**Status:** Governing implementation sequence  
**Date:** 29 October 2026
**Scope:** Responsive web SaaS; no native mobile application in P1. Product source: updated LedgerPilot Complete SRS.

## 1. Review and sequencing decision

**Phase 2–3 audit follow-up (29 October 2026):** See [the audit and repair record](phase-2-3-audit.md). Added regression protection for reset/login races, backdated advance over-allocation, reversal chronology, date-scoped close readiness and malformed CSRF headers. Migration/readiness is now **0020**. These are repairs within the existing architecture, not a new phase or a change to the delivery sequence. Final audit verification is recorded in that document; Phase 4 remains the next separate gate.

The existing LedgerPilot plan contains the right accounting foundations, product modules, security concerns and deployment progression. It does not fully enforce the developer sequence you requested: backend work is spread across phases, frontend implementation is not gated on completion and testing of all P1 APIs, and API testing, UI testing, integration testing and deployed E2E testing are not cleanly separated.

This revision reorders execution around the project's dependencies. It preserves LedgerPilot's SRS architecture and P1/P2/P3 boundaries. The uploaded Updated_Implementation_Plan.md is for an unrelated event-driven task-processing product. Its task-engine requirements and technology choices are excluded; only its clear use of phases and exit gates informed the presentation.

| Requested sequence | Existing plan assessment | Revised treatment |
|---|---|---|
| Project structure, uv, dependencies first | Present early, but mixed with other work | Phase 0 is repository, uv, packages, lockfiles, local DB and CI |
| Set up DB/auth/service keys second | Configuration exists, but Supabase/Auth ownership is ambiguous | Phase 1 provisions isolated services and secrets; resolves identity decision |
| Full backend and all frontend-required APIs | Backend split across accounting/banking/document phases | Phases 2–3 complete all approved P1 models, schemas, services, routers, APIs and report endpoints before UI |
| Pytest and Swagger UI test all APIs | Tests are distributed without an all-P1 gate | Phase 4 is a dedicated backend/API release gate |
| Frontend, UI tests, API integration, integration testing | Some activities are implicit/mixed | Separate Phases 5, 6, 7 and 8 |
| Deployment then system/E2E | Partly combined with pre-deploy acceptance | Phase 9 deploys tested demo; Phase 10 tests deployed system |
| SDK/public API only after end-to-end success | Public platform is later in SRS, boundary unclear | Phase 11 starts only after deployed E2E sign-off |

“Complete backend first” means complete for approved P1 scope, not all 44 SRS functional requirements. The SRS defers some capabilities to P2/P3/P4; implementing those before initial UI would change release scope. “API after E2E” means the external developer API and SDK. The internal REST API needed by LedgerPilot's own frontend must be created and verified earlier.

## 2. Architecture and rules this reordering preserves

This plan changes delivery order, not system design.

- Keep the SRS architecture: Python/FastAPI modular monolith, PostgreSQL, SQLAlchemy/Alembic, React/TypeScript/Vite web client and the defined AWS production path.
- Supabase may host P1 PostgreSQL and private object storage if current limits, security and recovery needs fit. Do not replace the SRS production architecture without an ADR.
- SRS identity is owned by FastAPI (password, MFA, sessions and audit controls). Supabase Auth would replace that design; select only one identity authority. If Supabase Auth is desired, approve an ADR and update SRS, session contracts and acceptance tests first.
- Never expose Supabase service-role, database, signing or provider keys in the browser. Service-role bypasses RLS. Use least privilege and test actual tenant isolation through the backend connection path.
- Use double-entry journals, exact decimal/currency rules, atomic/idempotent posting, immutable posted records, linked reversals, period locks and subledger tie-outs.
- A bank transaction is evidence of cash movement, not automatically income, expense, loan principal, tax or transfer. Uncertain items stay in review.
- AI/OCR may propose extraction, match or category with source evidence. It does not post financial entries in P1. Manual workflows must work when AI or any provider is unavailable.
- P1 is a responsive web product only. Use manual CSV bank imports; no native mobile, live money movement or tax filing.
- Free hosting is a quota-limited demonstration only when current provider eligibility permits it. It is not free commercial hosting or a production availability promise.
- Ten million registered users and ten million requests per minute are separate future targets. Neither is a P1 promise; capacity must be measured.

## 3. Project structure, stack and bootstrap

Create one repository with clear backend, frontend, tests, deployment and documents boundaries:

~~~text
ledgerpilot/
├── README.md  CONTRIBUTING.md  SECURITY.md  .gitignore  .env.example
├── docker-compose.yml
├── docs/{SRS,Implementation_Plan.md,adr,api,runbooks}/
├── backend/
│   ├── pyproject.toml  uv.lock  .python-version  alembic.ini
│   ├── migrations/versions/
│   ├── src/ledgerpilot/
│   │   ├── main.py  config.py  api/v1/
│   │   ├── identity/ tenancy/ accounting/
│   │   ├── sales/ purchases/ banking/ documents/
│   │   ├── reports/ notifications/ audit/ shared/
│   └── tests/{unit,property,api,integration,security,fixtures}/
├── frontend/
│   ├── package.json  package-lock.json  vite.config.ts
│   └── src/{app,features,components,api,hooks,forms,routes,test}/
├── tests/{contracts,integration,e2e,system}/
├── deploy/{docker,demo,production}/
└── .github/workflows/
~~~

Routers handle HTTP only; application services own workflows and accounting rules; repositories own persistence; Pydantic defines external contracts; SQLAlchemy models persistence. Only the posting service writes posted journals. Every request, report, background job, file, search and export is tenant scoped. Migrations are the schema-change mechanism.

| Layer | P1 selection | Cost note |
|---|---|---|
| Backend/runtime | Python, FastAPI, Pydantic v2, Uvicorn | Open-source |
| Project manager | uv, pyproject.toml, uv.lock | Free |
| Data | PostgreSQL, SQLAlchemy 2, Alembic, psycopg; Supabase host optional | Software free; hosted quotas/backup limits apply |
| Identity | FastAPI identity per SRS, Argon2id, TOTP, secure cookies/CSRF | Libraries free; email/domain may cost |
| Web | React, TypeScript, Vite, Tailwind, shadcn/ui | Open-source |
| Forms/API state | React Hook Form, Zod, TanStack Query | Open-source |
| API/test | OpenAPI 3.1, Swagger UI, pytest, httpx, Hypothesis, Playwright, axe | Free tools; hosted CI has quotas |
| Local services | Docker Compose PostgreSQL; optional MinIO/Mailpit | Free software, local compute required |
| First deploy | Vercel web + AWS EC2 API demo; Supabase if approved | Quota-limited; free eligibility/credits vary; demo only |
| Production | SRS AWS path: ALB/app cells, RDS/Aurora, S3/KMS, Secrets Manager; SQS when justified | Paid |
| AI | Rules/manual review first; optional local OCR/model adapter | Open weights do not make inference compute free |

Pin compatible versions at implementation time. Example bootstrap (verify the Python version against the SRS and libraries before pinning):

~~~powershell
git init
uv init --package --name ledgerpilot-api backend
Set-Location backend
uv python pin 3.12
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings sqlalchemy alembic psycopg email-validator argon2-cffi pyotp python-multipart
uv add --dev pytest pytest-asyncio httpx hypothesis ruff mypy coverage
uv sync
uv run fastapi dev src/ledgerpilot/main.py
~~~

Scaffold the web client from the repo root with Vite React TypeScript, commit package-lock.json, and use npm ci in CI. Add optional OCR, mail, bank or AI libraries only in the phase that needs them. Tests must run without paid provider credentials.

## 4. Phase 0 — Repository and uv setup

**Purpose:** Create a reproducible project foundation before product code.

1. Confirm repo root, branch/review policy, SRS version and approved P1 scope.
2. Create the repository structure, .gitignore, .env.example and README setup guide.
3. Initialize installable backend with uv, pin Python, add baseline runtime/dev libraries and commit uv.lock.
4. Create React/Vite/TypeScript shell and lockfile; do not build product screens yet.
5. Add Docker Compose PostgreSQL with an environment-based health check, migration directory, and a confirmation-gated reset procedure for disposable local data. Add accounting seed fixtures alongside the Phase 2 domain schema so Phase 0 does not invent financial data before its rules exist.
6. Add GitHub Actions for locked backend checks and tests, frontend production build/artifact and API container build; gate AWS demo deployment on passing CI and GitHub OIDC. Configure Vercel Git integration for web previews and production deploys.
7. Record exact runtime/library versions, ADR format, and SRS-linked issue/PR templates.
8. Document fresh clone, install, database start, migration, test and app start commands.

**Deliverables:** Repository scaffold, locked backend/frontend dependencies, contributor and SRS-linked issue/PR templates, local PostgreSQL Compose configuration and reset instructions, API container, CI/CD definitions, and clean-clone guide.  
**Exit gate:** A clean checkout installs and builds both apps; backend quality tests pass; PostgreSQL starts healthy from Compose; API image builds; GitHub CI passes on the selected default branch; provider/deployment setup is documented; no secret is committed.

## 5. Phase 1 — Database, authentication and service configuration

**Purpose:** Set up selected services and keys before database-backed feature work.

1. Use FastAPI-owned authentication; Supabase Auth is not in scope unless an ADR and SRS/session contract are approved. Follow the SRS: 10-minute access-token expiry, HttpOnly/Secure cookie on the API origin, CSRF and strict Origin checks, no bearer token in localStorage, refresh rotation, revocation, MFA and audited recovery. The selected design is short-lived JWT access plus server-stored, hashed, rotated refresh tokens.
2. Use the existing local Docker Compose PostgreSQL for this phase. Do not provision a hosted database now; revisit hosted PostgreSQL at deployment after current cost, quota, network, backup and recovery review.
3. Separate database roles: Compose bootstrap administrator, least-privileged application runtime role, and schema migration role. The API uses only the runtime role; migration credentials stay in an operator/migration context. Select pool behavior and exact grants, then verify privilege separation.
4. Defer tenant RLS policies and real-session cross-tenant tests until Phase 2 creates the tenant schema. Do not claim tenant isolation from an empty database.
5. Configure private storage only if P1 upload use requires it; short-lived access, content/size validation and scan before parse. Disable hosted upload if free profile cannot support safe processing.
6. Inventory only selected values: database URLs, migration URL, app signing secret, CORS origins, optional storage/email/telemetry credentials. P1 tests need no paid AI key.
7. Keep local values in ignored .env; keep deploy secrets in protected provider settings. No private value in VITE_ variables.
8. Document owner, purpose, environment, quota, expiry, rotation/revocation and fallback for each credential.
9. Verify connectivity, startup validation, redaction and credential rotation.

**Deliverables:** [Phase 1 service setup and credential inventory](phase-1-service-setup.md), [approved browser auth contract](auth-session-contract.md), local credential helper and runtime validation, verified runtime/migration roles and connection strings, real role/password-rotation checks and CI definitions.
**Exit gate:** Local PostgreSQL is healthy; app and migration roles are separate and verified; the API runtime uses only the restricted app role; secrets stay local and ignored; free local test workflow is documented. Tenant RLS and cross-tenant tests are a Phase 2 exit gate after tenant schema exists.

## 6. Phase 2 — Backend foundation and accounting core

**Purpose:** Implement secure shared backend and ledger foundations.

1. Add typed settings, app lifespan, DB session, request IDs, structured errors and redacted logs.
2. Create versioned /api/v1 foundation, auth dependencies, health/readiness, pagination and validation envelope.
3. Configure Alembic; create models/migrations for identity, businesses, memberships, roles, audit, accounting policy and chart of accounts.
4. Implement currency precision, exact decimal arithmetic, deterministic rounding, fiscal periods, timezone and basis settings.
5. Implement immutable journal/line records, atomic balanced posting, idempotency, optimistic version, locked-period rejection and linked reversals.
6. Implement GL, trial balance and subledger/control-account reconciliation.
7. Apply database constraints, tenant context and RLS/grants. Only posting service can write posted journals.
8. Add synthetic fixtures and independent expected results for the accounting golden scenarios.

**Deliverables:** SQLAlchemy models, Pydantic schemas, migrations, repositories/services, identity/tenant foundations, journal engine and security tests.  
**Exit gate:** Clean migration, balance, immutability, idempotency, period-lock and cross-tenant denial tests pass on PostgreSQL.

## 7. Phase 3 — All P1 backend modules and APIs

**Purpose:** Complete backend models, schemas, services, routers and every API needed by P1 screens before frontend feature work.

Build in dependency order: party/customer/vendor roles; sales receipts/invoices/tax/credits/refunds; receipt allocation and AR aging; supplier bills/credits/partial payments and AP; loan principal/interest/fees/schedules; bank CSV mapping/preview/import/deduplication; transaction review/categorize/match/transfer/card settlement/split/exclude/rules; statement reconciliation and sign-off; reports and dashboards; evidence/document intake; notification preferences; and P1 AI proposal endpoints where approved.

For every module:

- Deliver persistence model and migration, request/response schemas, service, repository, router, authorization, audit event, tests and OpenAPI.
- Build frontend-facing endpoints for all P1 screens, KPIs, graphs, filters, pagination and report drill-down. The backend owns financial calculations and query scoping.
- Return basis/date/timezone/currency context on reports and charts. Prevent unsupported chart rollups from inventing financial semantics.
- Use idempotency keys on financial POSTs and version checks on concurrent edits. Do not leak raw DB/provider errors.
- Keep unknown deposits and low-confidence AI/document results in review; never silently classify to revenue or post.
- Keep later SRS modules implemented only when their release phase is approved. Feature flags do not replace server authorization.

**Deliverables:** Complete P1 internal REST API, OpenAPI 3.1, endpoint-to-FR/UC/TC matrix, migrations and synthetic data.  
**Exit gate:** Every P1 UI/report action has an API and acceptance test; financial effects are atomic, idempotent, auditable and tenant authorized.

## 8. Phase 4 — Backend verification: pytest and Swagger UI

**Purpose:** Prove all P1 APIs before frontend feature development.

1. Map each P1 FR/UC/VAL/NFR/SEC to routes, automated tests and expected accounting effects.
2. Run pytest unit/property/API tests for valid, invalid, unauthorized, forbidden, duplicate, stale, cross-tenant, rollback and retry cases.
3. Use real PostgreSQL integration tests for migrations, constraints, transactions, RLS and concurrency.
4. Independently verify expected balances/report totals, including cash/accrual timing, $100 invoice with $40 payment leaving $60 due, $100 loan receipt and principal repayment with interest separate, transfer, unknown deposit, tax remittance and reconciliation.
5. Test migration from empty/current supported schema and restore of synthetic backup.
6. Review generated OpenAPI for all routes, status/errors, request/response, security, pagination and idempotency; generate typed TS client.
7. Manually execute representative flows in Swagger UI against local/test only. Keep interactive docs private or disabled in public deployment; Swagger does not replace pytest.
8. Record build, fixture version, result and evidence; planned tests are not passing evidence.

**Deliverables:** Passing pytest suite, route/test matrix, reviewed OpenAPI, generated client and Swagger verification record.  
**Exit gate:** All P1 API, accounting and authorization tests pass; no critical/high issue; API contract stable. Frontend feature work waits on this gate.

## 9. Phase 5 — Build responsive web frontend

**Purpose:** Implement P1 screens against the verified API contract, initially using mocks.

1. Follow the SRS screen registry: onboarding, business context, dashboard, accounts, transaction review, customers/invoices, vendors/bills, loans, CSV import, reconciliation, reports, evidence and settings.
2. Build accessible navigation, components, forms, tables, filters, empty/loading/error states and responsive web layouts.
3. Use generated API types and mock adapter with synthetic records. Charts consume API-shaped fixtures and show basis/date/currency/data freshness.
4. Server calculations are authoritative; UI does not calculate accounting balances from raw transaction lists.
5. Implement session UX per approved auth design. No token in localStorage and no service secrets in frontend.
6. Add keyboard/contrast/focus/labels/reduced motion and screen-reader feedback. Do not scaffold native mobile apps.

**Deliverables:** P1 web UI and production build, mock adapter, accessible component set.  
**Exit gate:** P1 screens and states pass build with mocks; no private credentials or unverified financial logic in UI.

## 10. Phase 6 — UI-only testing

Test fields, formatting, filters, permissions presentation, dialogs, loading/empty/error states and charts with unit/component tests. Run Playwright against mocked APIs for core journeys, 401/403/conflict/timeout/retry states and supported browser sizes. Check keyboard, focus, labels, contrast and reduced motion using axe plus manual review. UI hiding is not backend authorization.

**Deliverables:** Frontend test and accessibility reports.  
**Exit gate:** UI workflows pass independently with mocks; no blocking navigation/accessibility defect.

## 11. Phase 7 — Integrate frontend and APIs

Replace mock operations incrementally through the generated client. Configure exact CORS, secure cookies/CSRF and session lifecycle. Integrate identity/business selection first, then sales, purchases, loans, bank review, reconciliation and reports. Map typed errors, conflicts and correlation IDs to the UI. Retry only safe/idempotent requests; never blindly retry a financial POST. Use short-lived upload access and show scan status. Disable mocks in release builds.

**Deliverables:** Integrated P1 web client, environment configuration and contract mismatch log.  
**Exit gate:** Every P1 workflow uses the real API; no mock-only screen or direct browser DB access remains.

## 12. Phase 8 — Integration testing

Run browser + API + PostgreSQL tests with isolated tenants and synthetic data. Verify accounting golden scenarios, report tie-outs, permissions/isolation across APIs/files/search/jobs/exports, duplicate requests, retries, stale edits, rollback, migration/restore, document scan/manual fallback and optional sandbox adapters. Check API/client compatibility and that model/provider outage leaves core bookkeeping usable. Fix critical/high issues and unexplained balances; attach test evidence to SRS IDs.

**Deliverables:** Integration report, security review, migration/restore evidence and release candidate.  
**Exit gate:** Integrated P1 tests pass from a clean environment; accounting ties, tenant boundaries hold and candidate is reproducible.

## 13. Phase 9 — First deployment: free quota-limited demo

Deploy the tested web client to Vercel through its Git integration and deploy the FastAPI API demo to AWS EC2 through GitHub Actions, Amazon ECR and Systems Manager. CI must pass before the AWS deploy job runs. GitHub OIDC supplies short-lived AWS credentials; no long-lived AWS access keys belong in repository secrets. The EC2 instance, ECR repository, least-privilege IAM roles, protected GitHub demo environment, API environment file, TLS reverse proxy and DNS require one-time setup and are documented in the root README. The deployment uses immutable commit-SHA images, health checks and rollback to the prior image. This EC2 flow is an implementation of the quota-limited demo only; it does not replace the SRS production design. Recheck provider quotas, inactivity, backups, domain and eligibility on launch day. Use synthetic data, separate demo secrets, TLS, strict CORS, secure cookies, rate limits, quotas, MFA and export/disable procedures. Keep the database private. Disable real bank feeds, money movement, tax filing, public SDK and unapproved AI. Verify auth, tenant isolation, financial flows, reports, export, quotas, redacted logs and a secret-free bundle. State demo limits clearly.

**Deliverables:** Versioned deployment, smoke results, rollback/export steps and visible limitations.  
**Exit gate:** Deployed version equals tested candidate; no real customer books; secrets private; demo can be disabled and data exported. Free-tier status is never represented as production guarantee.

## 14. Phase 10 — Deployed system and E2E testing

Against the deployed URL, run full synthetic-tenant browser E2E for account/business setup, MFA, invoice/receipt/payment, bills, loan, CSV, review, reconciliation, reports and export. Verify TLS, cookies/CSRF, CORS, provider credentials, RLS, upload scan, rate limits, auth expiry, isolation and support/admin access. Test enabled email/storage/provider adapters, outage and revoked keys. Restore backup to isolation and tie balances/audit. Run demo-bounded load tests only; never use free hosting to claim 10M/min. Accountant reviews selected policy/jurisdiction scenarios. Capture deployment ID, browser, fixture, result, defect and evidence.

**Exit gate:** All critical P1 paths pass in deployment; no unexplained balance or critical/high issue; restore/export work; named product/accounting/security owners sign off. Phase 11 is blocked until then.

## 15. Phase 11 — Public developer API and SDK

This is the later SRS P2/P3 platform, distinct from internal P1 APIs. Confirm demand/legal scope/support first. Define versioning, scopes, rate limits, pagination, idempotency, errors, deprecation and compatibility. Add hashed/rotatable/revocable credentials, audit, abuse controls, signed webhooks with replay defense/retry and delivery history. Publish reviewed OpenAPI, changelog and synthetic sandbox. Build SDKs only for validated demand (Python/TypeScript are candidates) with typed errors, timeouts, safe retries, pagination and examples. Do not expose admin routes or raw ledger queries.

**Deliverables:** Public versioned API, key controls, webhooks, developer docs and selected SDK package.  
**Exit gate:** Security, compatibility, rate-limit, docs and developer-pilot acceptance pass.

## 16. Phase 12 — Commercial production, scale and ongoing operations

This is a funded continuation, not a free demo. Select jurisdiction, data residency, retention, support/on-call and incident policy. Move to private managed PostgreSQL and the SRS AWS production design with encrypted storage, secrets/KMS, backups/PITR, queue/observability as measured. Enable partner feeds/email/billing only after contract, sandbox, consent and security gates. Measure active users, writes, documents, report load, peak RPS, storage and cost separately. Add cells, replicas, partitioning/sharding/cache only after benchmarks show a need. Test failover, restore, replay, provider outage and tenant isolation under production-like load. Publish SLOs only with evidence and staffed operations. Review access, dependencies, retention, restore, import quality, AI proposals, errors, cost and feedback regularly. Architecture/accounting/auth changes require ADR and SRS update.

**Exit gate:** Funded operation, recovery, security and support exist; measured capacity/cost meets the approved workload. Ten million users is not equivalent to ten million requests/minute (~166,667 requests/second peak); publish only measured claims.

## 17. SRS requirement traceability

| Requirement groups | Main phases |
|---|---|
| FR-001–004 identity, tenancy, policy, ledger | 1–4; security reverified in 8 and 10 |
| FR-005–007 sales, receivables, purchases, payables | 3–8 |
| FR-008, FR-010 bank intake and categorization | 3–8 |
| FR-009 document evidence | 3, 5–10; manual fallback remains |
| FR-011/012 loans, transfers and card settlement | 2–8 |
| FR-013/014 reconciliation, reports and tax support | 3–10 |
| FR-015 categorization automation | P1 suggestions/approval; later automation only by SRS gate |
| FR-016 onward | Follow explicit SRS release tags; do not silently expand P1 |
| FR-018 integrations/public developer platform | Public API/SDK Phase 11, partner approval required |
| VAL/NFR/SEC | Designed from Phase 0, exercised in Phases 4, 6, 8 and 10 |

For each work item record SRS ID, release, dependency, expected journal, authorization, automated test and actual evidence. This map is an index, not approval to ship later-phase features.

## 18. Quality gates and project workflow

Every PR: SRS-linked acceptance, review, locked dependencies, Ruff/type checks, pytest/coverage, migration check, frontend production build, API container build, secret/dependency scan and test evidence. Protect the default branch and require backend, frontend and container CI checks before merge. Vercel deploys web previews/production through Git integration. The AWS demo deploys from the default branch after CI passes, using OIDC, ECR and SSM; deployment requires protected environment variables and a bootstrapped EC2 host. Every release: compatible additive migration, OpenAPI compatibility, auth/TLS/CORS/cookie checks, tenant tests, backup/rollback/export plan and core workflow smoke tests.

Accounting blockers: unbalanced or duplicate posting, broken subledger tie-out, silent unknown-deposit revenue, period-lock bypass, cross-tenant access or unexplained migration balance. Swagger UI supplements automation; UI, integration and deployed E2E each require separate evidence.

Kanban: Backlog → Ready → In Progress → Review → Verification → Done. Ready requires settled scope, dependency, security impact, fixtures and acceptance. Done requires review and phase-specific evidence, not just completed code.

## 19. Launch checklist and change record

- [ ] Clean clone, uv lock and local DB setup work from documentation.
- [ ] P1 features and later SRS scope are distinguished.
- [ ] Selected identity ownership, session lifecycle, tenant RLS, service roles and key rotation are verified in their implementation phases.
- [ ] All P1 models, schemas, services, routers, report and dashboard APIs complete before UI.
- [ ] Pytest and Swagger/OpenAPI cover every P1 API before frontend.
- [ ] Frontend and independent UI tests pass before API integration.
- [ ] Integration tests pass before deployment.
- [ ] Demo quotas, eligibility, synthetic data, export and rollback are clear.
- [ ] Deployed E2E, restore, security and accountant review pass before SDK/public API.
- [ ] No native app, real money movement, tax filing or unapproved future capability enters P1.
- [ ] Production budget, support and on-call exist before real commercial customers.
- [ ] GitHub branch protection requires the backend, frontend and API container checks before merging to the selected default branch.
- [ ] Vercel project is linked to GitHub with frontend/ and dist settings and the selected default branch as production.
- [ ] AWS demo ECR, EC2/SSM, OIDC role, protected demo environment, TLS hostname and runtime configuration are provisioned before enabling deployment.

| Version | Date | Change |
|---|---|---|
| 1.0 | 26 Oct 2026 | Initial plan aligned to LedgerPilot SRS |
| 2.0 | 27 Oct 2026 | Resequenced plan to match LedgerPilot scope |
| 2.1 | 27 Oct 2026 | Added project standards and ADRs |
| 2.2 | 27 Oct 2026 | Added progress tracker and demo CI/CD |
| 2.3 | 27 Oct 2026 | Phase 0 scaffold; Docker/hosted CI pending |
| 2.4 | 27 Oct 2026 | Began Phase 1 setup |
| 2.5 | 27 Oct 2026 | Completed local Phase 1 configuration and live database verification |
| 2.6 | 27 Oct 2026 | Audited configuration, bootstrap and CI; verified API image |
| 2.7 | 29 Oct 2026 | Recorded user-confirmed commit and passing GitHub checks; closed Phase 0; Phase 2 ready |
| 2.8 | 29 Oct 2026 | Completed Phase 2 identity, tenant and accounting foundation; real-service acceptance and container verification |


## 20. Current implementation progress tracker

Track actual code and verification here; a planned item is not complete until its evidence exists. Update this section and the version history whenever implementation changes.

**Current phase:** Phases 2 and 3 are complete for the documented local P1 implementation scope after the [audit repairs](phase-2-3-audit.md); **Phase 4 is next and has not started**. Fresh all-service acceptance passed **110 tests in 572.40 seconds**. Ruff, strict mypy, non-root API build/HTTP/readiness/secure-cookie checks, locked dependencies, compilation and whitespace checks pass. See [the implementation record](phase-3-business-modules.md), [API inventory](contracts/phase-3-api-coverage.md) and [completion contract](contracts/phase-3-completion.md). Reviewed duplicates/rule suggestions, resumable encrypted exports, opening corrections/cash policies, independent manual privacy fulfillment and complete response contracts preserve the modular monolith and PostgreSQL financial authority. Main schema is **0020** with existing data preserved. OpenAPI is unchanged at 121 paths and 229 schemas; CI includes all real-service test modules. ADR-010's audit amendment and ADR-011–013 record implementation details and local limits. Phase 4 independently verifies backend/Swagger/restore and generates the frontend client before frontend feature work. Production capacity and automated P2 privacy execution are not claimed. No commit, push or new hosted CI run was performed. Earlier completion evidence remains historical; the audit record contains the latest result.

**Closure evidence:** User confirmation in this conversation on 29 October 2026 is the source for the GitHub result; no Actions run URL was supplied or independently inspected in this documentation update. The local checkout is on `main`, with latest commit `13402b1b8fd8b198f3f9bc57dec086475754d153` (`Phase 1 : completed the project structure and the database connections are done and tested`). This identifies the observed checkout; the exact CI run-to-commit association was not independently verified. The Phase 0/1 audit and its identified fixes are complete. Branch protection and cloud deployment are separate outstanding rollout tasks.

**Phase 1:** Complete for the selected local services. FastAPI-owned JWT access cookies and stored rotating refresh tokens are documented; the local PostgreSQL runtime/migration roles, private credential file, configuration validation, denied-operation checks and password-rotation drill pass. Phase 1 initially used loopback port 55432; Phase 2 moved the same database volume to 127.0.0.1:15432 after Windows rejected the previous binding. No hosted service was provisioned. The previously deferred auth endpoints, tenant schema/RLS and bounded API pool are now implemented in Phase 2. See `docs/phase-1-service-setup.md` and `docs/auth-session-contract.md`.

**Implemented in this update:**

- `.github/workflows/checks.yml` and `quality.yml`: shared locked backend checks/tests, frontend production build/artifact, API container build, and gated AWS demo deploy after required CI jobs.
- `.github/workflows/release.yml`: required checks before version-tagged API image publishing to GitHub Container Registry.
- `.github/ISSUE_TEMPLATE/feature_request.yml`, `bug_report.yml`, and `.github/pull_request_template.md`: SRS, accounting, tenant/security and evidence prompts.
- `backend/Dockerfile` and `backend/tests/test_health.py`: non-root API image health check and a liveness route contract test.
- `deploy/aws/deploy-api.sh`: EC2-side ECR pull, health verification and rollback to the previous image on failure.
- `scripts/reset-local-db.ps1` and `docker-compose.yml`: environment-based PostgreSQL health check and an explicit-confirmation reset for disposable local database data.
- `backend/db/init/01-roles.sql` and `scripts/bootstrap-local-db.ps1`: separate local runtime/migration roles, schema privileges and non-destructive bootstrap for an existing local database volume.
- `backend/src/ledgerpilot/local_setup.py`: local-only credential preparation, atomic file updates, configurable loopback port, hidden-value validation, real role/grant probes and a temporary password-rotation drill with restoration.
- `backend/src/ledgerpilot/config.py` and configuration tests: required secrets, restricted runtime role, valid origins, verified TLS for hosted profiles and bounded Phase 2 pool settings.
- `.env.example`, ignored `.env`, `docs/phase-1-service-setup.md` and `docs/auth-session-contract.md`: selected local credentials, ownership/rotation/fallback inventory and the approved browser session contract. Existing valid settings were preserved; undersized bootstrap credentials were replaced before the first local database initialization.
- `.github/workflows/quality.yml`: isolated PostgreSQL initialization, real grants/password-rotation verification and disposable CI cleanup. Hosted checks passed according to the user confirmation recorded above.
- `CONTRIBUTING.md`, `.nvmrc`, `.gitattributes`, and `README.md`: reproducible setup, branch/review expectations, local commands, Vercel/AWS setup, and deployment boundaries.

**Verification recorded:** 38 tests pass, including five real PostgreSQL bootstrap rejection cases. Ruff, mypy, frontend production build, database grant/DDL-denial verification, API image build and container HTTP/health/OpenAPI checks pass. All workflows pass actionlint. Prior Phase 1 password-rotation evidence remains recorded. Credentials remain ignored. The user subsequently confirmed passing GitHub checks on 29 October 2026; deployment remains unverified. These local checks were not rerun for this documentation-only update.

**Audit fixes:** Block connection query overrides; isolate file validation; reject unsafe SQL bootstrap inputs; restrict Docker context; require shared checks before release; publish the built image; gate demo deployment on an explicit enable flag and default branch, validating environment values inside the job. See [Phase 0 and Phase 1 audit](phase-0-1-audit.md).

**Phase 2 implementation:**

- `backend/src/ledgerpilot/database.py`, `main.py`, `shared/`: app lifecycle, bounded pooled transactions, actual runtime-role checks, readiness, CSRF/body/depth limits, structured safe errors/logs, strict schemas and read contracts.
- `identity/`: real SMTP verification/reset, Argon2id and offline blocklist, ten-minute JWT access, stored rotating refresh families, replay revocation, seven-day absolute expiry, privileged idle limit, encrypted one-use TOTP, hashed recovery codes, CSRF cookies and distributed auth throttling/backoff.
- `tenancy/`: verified memberships, forced RLS, one-use 48-hour invitations, multi-business selection and prospective policy history. No unrestricted support role is exposed.
- `accounting/`: typed accounts and versions, non-overlapping periods, durable versioned drafts, a shared private posting gateway, immutable balanced posting, idempotency, reversals, provenance/period/policy links, exact rounding/allocation, GL, closing-balance trial balance and reconciliation primitives that reject incomplete sources.
- `backend/migrations/`: operator-only Alembic setup and frozen revisions `0001`–`0005`. Financial history is protected from destructive downgrade. Source SQL and migrations are included in the API image; runtime never receives migration credentials.
- `docker-compose.auth.yml`, `.env.example`, local setup helper and ignored `.env`: isolated loopback SMTP, dedicated MFA key/initialization marker, explicit cookie/mail settings and preserved existing database volume/secrets.
- `backend/tests/test_phase2_security.py`, `test_phase2_integration.py`, `.github/workflows/checks.yml`: unit/property checks and actual PostgreSQL/SMTP acceptance in a uniquely created temporary database, including draft isolation, concurrency, replay, reset, role denial and safe cleanup.
- [Phase 2 implementation and operations](phase-2-backend-foundation.md), [ADR-010](architecture/ADR-010-phase-2-auth-and-posting.md) and the updated auth contract record scope, requirements, setup, security details and later-phase boundaries.

**Phase 2 verification:** 73 tests passed against the final application source, including real PostgreSQL and SMTP flows; coverage is 86%. Ruff and mypy (45 source files) passed. Migrations through 0005 applied successfully, including repeated upgrade checks. The final non-root API image passed live/readiness, OpenAPI, secure-cookie and migration-asset smoke checks; root-owned application code is not runtime-writable. All three workflows passed actionlint. The future-migration template rendered and compiled successfully; unimplemented generated migrations fail explicitly. The independent mailbox HTTP/SMTP checks passed. Updated CI now enables the new real-service tests; these new changes have not been run on hosted GitHub Actions yet. No hosted deployment or remote push is part of this update. Real browser HTTPS/cookie/CORS acceptance remains in UI integration/deployed testing; actual AR/AP/loan/tax source adapters and remaining P1 feature APIs are Phase 3 work.

**Phase 2 execution order:**

1. Implement application lifespan, the bounded PostgreSQL engine/session factory, transaction handling, request IDs, redacted logs/errors, and separate liveness/readiness checks.
2. Configure Alembic and implement identity, business, membership, role and audit models/migrations; deliver the approved login/session/MFA/recovery controls.
3. Enforce tenant authorization, transaction-scoped tenant context, RLS and explicit grants using the restricted runtime connection.
4. Build accounting policy, chart of accounts, currency/rounding, fiscal periods, immutable balanced journal posting, idempotency, reversals, GL and trial balance.
5. Pass real PostgreSQL migration, accounting-invariant, concurrency and cross-tenant tests before closing Phase 2. Track work as implemented only when its checks pass.

**Pending to activate deployment:**

- Protect the selected default branch with required checks shown by GitHub for the reusable workflow. The remote is configured and the user has confirmed passing hosted checks; branch protection configuration is not confirmed.
- Link Vercel to the repository, set the project root to `frontend`, build command to `npm run build`, output directory to `dist`, and production branch to the selected GitHub default branch.
- Provision the demo ECR repository and SSM-managed EC2 instance; install Docker, AWS CLI, `/opt/ledgerpilot/deploy-api.sh`, root-only API environment settings, TLS reverse proxy and DNS.
- Configure the narrowly scoped GitHub OIDC role and the `demo` environment variables listed in the README.
- Perform a deployed smoke test and verify rollback before marking Phase 9 complete.


### Official references

Verify package versions and provider terms when implementing:

- [uv projects](https://docs.astral.sh/uv/guides/projects/) and [installation](https://docs.astral.sh/uv/getting-started/installation/)
- [FastAPI tutorial](https://fastapi.tiangolo.com/tutorial/)
- [SQLAlchemy](https://docs.sqlalchemy.org/en/20/) and [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [Supabase documentation](https://supabase.com/docs)
- [Vercel Vite deployment](https://vercel.com/docs/frameworks/frontend/vite)
- [AWS database migration guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/strategy-database-migration/welcome.html)
- LedgerPilot Complete SRS, updated edition: docs/srs/LedgerPilot_Complete_SRS_Updated.docx




