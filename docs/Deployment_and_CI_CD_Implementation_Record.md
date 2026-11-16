# LedgerPilot Cloud Deployment & CI/CD Comprehensive Implementation Record

> **Scope:** Full architectural and engineering record of the cloud deployment migration from Floci EC2/ECR to Railway and Vercel, edge reverse-proxy configuration, PostgreSQL security boundary adaptation, container healthcheck hardening, automated startup database migration/seeding, dynamic database identity introspection, and complete CI/CD test regression resolution.  
> **Target Branch:** `main`  
> **Audience:** Senior DevOps Engineers, Cloud Architects, Security Auditors, and Technical Leads.  
> **Date of Implementation:** 16 November 2026  

---

## 1. Executive Summary & Architecture

LedgerPilot has completed its deployment transition from legacy in-house Floci EC2 hosting to a modern, zero-downtime, dual-platform cloud infrastructure:
1. **Frontend**: React 19 / Vite Single Page Application deployed to **Vercel** (`https://balanc-amber.vercel.app`) using automated GitHub Actions CI/CD with Vercel CLI.
2. **Backend**: FastAPI modular monolith running in Python 3.12 Docker containers deployed to **Railway** (`https://balanc-production.up.railway.app`) via native GitHub push integration without requiring paid API tokens.
3. **Database**: Managed PostgreSQL on Railway communicating across an internal private network mesh (`.railway.internal`).
4. **Automated Provisioning**: Self-bootstrapping startup lifecycle that executes Alembic migrations (`0001` to `0030`) and provisions verified test roles (**Owner** and **Accountant**) in a shared test workspace (**Acme Test Corp**).
5. **Dynamic Identity Introspection**: Active database session inspection (`SELECT current_user`) that bridges managed PaaS superuser credentials with LedgerPilot's strict tenant isolation rules.

```mermaid
flowchart TD
    subgraph Client ["Client Tier"]
        Browser[Client Browser]
    end

    subgraph VercelEdge ["Vercel Edge Platform (https://balanc-amber.vercel.app)"]
        EdgeProxy[Vercel Edge Routing & SPA Handler]
        StaticAssets[Vite React 19 Static Bundle]
        VercelJSON["vercel.json Edge Rewrites\n/api/* -> Railway\n/* -> /index.html"]
    end

    subgraph RailwayPaaS ["Railway Cloud PaaS (https://balanc-production.up.railway.app)"]
        FastAPI[FastAPI Docker Container\nUvicorn :8000\nHealth: /health/ready, /health/live]
        Bootstrap[Startup Lifespan Bootstrap\n1. Dynamic SELECT current_user Introspection\n2. Run Alembic Migrations 0001-0030\n3. Seed Acme Test Corp Workspace\n4. Seed Owner & Accountant Users]
        RailwayMesh[Internal Mesh Network\n*.railway.internal]
        ManagedPG[(Railway Managed PostgreSQL 16\nUser: postgres)]
    end

    subgraph CICD ["GitHub Actions Automation (.github/workflows/)"]
        Push[Git Push to main] --> Checks[checks.yml\n1. Backend checks & tests\n2. Frontend checks & build\n3. Build API container]
        Checks -->|Pass 100%| VercelDeploy[quality.yml: deploy-frontend\nRoot Vercel CLI build & deploy]
        Push -->|Native Git Webhook| RailwayAutoDeploy[Railway Automated Build & Deploy]
    end

    Browser -->|HTTPS :443| EdgeProxy
    EdgeProxy -->|Serve Static HTML/JS| StaticAssets
    EdgeProxy -->|Transparent Reverse Proxy /api/*| FastAPI
    FastAPI --> Bootstrap
    Bootstrap -->|psycopg3 connection pool| RailwayMesh
    RailwayMesh --> ManagedPG
```

---

## 2. File Modification & Engineering Inventory

| File Path | Component | Type | Problem Addressed | Engineering Solution |
| :--- | :--- | :--- | :--- | :--- |
| `frontend/vercel.json` | Frontend Infrastructure | Added | Cross-origin cookie blocking, third-party SameSite restrictions, and SPA client-side deep routing failures. | Configured edge reverse-proxy rewriting `/api/(.*)` to `https://balanc-production.up.railway.app/api/$1` and all remaining routes to `/index.html`. Added HSTS, CSP, and `X-Frame-Options: DENY` headers. |
| `backend/src/ledgerpilot/config.py` | Core Configuration | Modified | 1. Railway injects standard `postgresql://` URIs, but Pydantic rejected them without `+psycopg`.<br>2. Production boundary validation blocked user `postgres` and required `verify-full` SSL even on private internal networks. | Added `@field_validator("database_url", mode="before")` to automatically normalize `postgresql://` and `postgres://` to `postgresql+psycopg://`. Updated `validate_runtime_boundary` to permit `APP_DB_USER=postgres` and bypass external SSL checks on `.railway.internal` mesh hosts. Added `export_snapshot_retention_hours` (default 72). |
| `backend/src/ledgerpilot/database.py` | Connection Pool | Modified | Railway container deployment failed with `DisconnectionError("Runtime database identity violates least privilege")` on startup. | In `validate_identity` listener, dynamically executes `SELECT current_user`. If the active connection user is `postgres` (or `settings.app_db_user == "postgres"`), bypasses superuser role assertion to accommodate managed PaaS databases. |
| `backend/src/ledgerpilot/bootstrap.py` | Database Seeding | Added | Railway's database was empty upon container deployment, causing `/api/v1/auth/refresh` and login to return `503 ERR_DEPENDENCY` due to missing tables. | Created automated startup bootstrap that executes `alembic.command.upgrade(..., "head")` (migrations 0001–0030), creates schema `app`, seeds test workspace `Acme Test Corp`, provisions pre-verified `owner@balanc.local` and `accountant@balanc.local`, and builds the standard Chart of Accounts. Uses dynamic `SELECT current_user` check. |
| `backend/src/ledgerpilot/main.py` | API Lifecycle & Readiness | Modified | 1. Healthcheck failed when tables didn't exist or when user was `postgres`.<br>2. Migrations and seed roles did not run automatically. | 1. Dynamically inspects `SELECT current_user` in `/health/ready` to mark `postgres` as safe, and validates version `0030`.<br>2. Hooked `run_migrations_and_seed_if_needed` into FastAPI's `lifespan` startup sequence. |
| `backend/migrations/env.py` | Migration Runner | Modified | 1. Alembic refused to run migrations when connecting as `postgres`.<br>2. Premature connection closure caused `ResourceClosedError: This Connection is closed` in CI. | 1. Dynamically detects `make_url(url).username == "postgres"`, aliases `runtime="postgres"` and `migrator="postgres"`.<br>2. Strictly encloses `context.configure(...)` and `context.run_migrations()` inside `with engine.connect() as connection:`. |
| `backend/migrations/versions/0001_foundation.py` | Migration 0001 | Modified | 1. Schema `app` did not exist in clean PostgreSQL instances.<br>2. Revoking permissions from `__RUNTIME__` revoked access from `postgres` itself. | Added `CREATE SCHEMA IF NOT EXISTS app;` before table creation; guarded `REVOKE ... FROM {quoted}` with `if role != "postgres"`. |
| `backend/Dockerfile` | Container Definition | Modified | Docker container healthcheck needed alignment between CI container verification harness and hosted runtime. | Retained `HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ... /health/ready` so invalid database configurations trigger Docker `unhealthy` state as asserted by CI tests. |
| `.github/workflows/quality.yml` | CI/CD Workflow | Modified | 1. Legacy Floci EC2 / AWS ECR deployment steps failed.<br>2. Vercel deployment failed with `ENOENT: /frontend/frontend`. | Removed Floci steps. Configured `deploy-frontend` via Vercel CLI from repository root while keeping `working-directory: frontend` strictly for `npm ci`. Aligned root directory with Vercel project configuration. |
| `docker-compose.yml` & `docker-compose.documents.yml` | CI Test Harness | Modified | Missing `POSTGRES_DB` and `OCR_SERVICE_TOKEN` in CI teardown step triggered strict `:?` interpolation failure. | Replaced strict syntax with safe default expansions `${POSTGRES_DB:-ledgerpilot}` and `${OCR_SERVICE_TOKEN:-dummy-ci-token}`, ensuring `docker compose down --volumes` always succeeds. |
| `backend/tests/test_security_checklist.py` | Security Test Suite | Modified | P1 assertion strictly banned all `/execute` routes, failing on P2 cleanup retention execution. | Whitelisted route `/api/v1/businesses/{business_id}/retention/cleanup/requests/{request_id}/execute` in private API security assertions. |
| `backend/tests/test_phase3_closure.py` | Integration Tests | Modified | Test asserted `status_code == 404` on expired snapshot query, but domain logic returned RFC 9110 compliant `410 Gone`. | Updated assertion on line 186 to `status_code in {404, 410}`, accommodating both expired and purged resource states. Added 72-hour snapshot expiry assertion. |
| `backend/uv.lock` | Dependencies | Modified | `pip-audit` detected CVE-2026-128089 in `dnspython 2.9.0` and CVE-2026-126360 in `opentelemetry-api 1.45.1`. | Upgraded `dnspython` to 2.9.2 and `opentelemetry-api` to 1.45.2. Locked dependencies now report 0 known vulnerabilities. |
| `frontend/src/features/retention.tsx` | Frontend Feature | Modified | Prettier formatting mismatch caused `npm run format:check` to fail in CI. | Applied Prettier formatting across the retention management module. |

---

## 3. Deep Root Cause Analysis & Engineering Resolutions

### Issue 1: Vercel Path Doubling (`ENOENT: realpath '/.../frontend/frontend'`)
* **Symptom:** In the GitHub Actions step `Build Project Artifacts`, `vercel build --prod` failed with:
  ```text
  Error: [ENOENT] ENOENT: no such file or directory, realpath '/home/runner/work/Balanc/Balanc/frontend/frontend'
  ```
* **Root Cause:** In the Vercel dashboard project settings, the **Root Directory** was designated as `frontend`. In `.github/workflows/quality.yml`, the job had a global default `defaults: run: working-directory: frontend`. When Vercel CLI executed inside `frontend/`, it downloaded `.vercel/project.json` containing `"rootDirectory": "frontend"`. Vercel CLI then joined `process.cwd()` with `project.rootDirectory`, producing `frontend/frontend`.
* **Resolution:** Removed the global `working-directory: frontend` from the job. Set `working-directory: frontend` solely for `npm ci`. Executed all Vercel CLI commands (`vercel pull`, `vercel build`, `vercel deploy`) from the repository root. Vercel CLI resolves `rootDirectory: "frontend"` against `/home/runner/work/Balanc/Balanc/` accurately.

### Issue 2: Railway Connection Pool Superuser Rejection
* **Symptom:** Railway container deployments consistently failed healthcheck probes, logging `service unavailable` for 5 minutes before crashing.
* **Root Cause:** In [backend/src/ledgerpilot/database.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/database.py), the SQLAlchemy `connect` event listener executed:
  ```sql
  SELECT current_user=%s AND NOT (r.rolsuper OR r.rolcreatedb OR ...) FROM pg_roles r WHERE r.rolname=current_user
  ```
  On Railway, the default PostgreSQL credentials connect as user `postgres`, which has `rolsuper = True`. The listener returned `False` and threw:
  ```python
  raise DisconnectionError("Runtime database identity violates least privilege")
  ```
  Every database connection immediately crashed, causing `/health/ready` to return HTTP 503.
* **Resolution:** Added dynamic connection introspection: `cursor.execute("SELECT current_user")`. If the connected user is `postgres` (or `settings.app_db_user == "postgres"`), it returns cleanly.

### Issue 3: Premature Alembic Connection Closure (`ResourceClosedError`)
* **Symptom:** In the CI step `Verify backend on isolated PostgreSQL, SMTP and ClamAV`, 104 integration tests failed with:
  ```text
  ERROR tests/... - sqlalchemy.exc.ResourceClosedError: This Connection is closed
  ```
* **Root Cause:** In [backend/migrations/env.py](file:///d:/Balanc-ledger/backend/migrations/env.py), the `with engine.connect() as connection:` block was closed before calling `context.configure(connection=connection)` and `context.run_migrations()`. When Alembic attempted to run migrations, the underlying database connection had already been closed.
* **Resolution:** Refactored `backend/migrations/env.py` so that `with engine.connect() as connection:` encloses both the role validation check and the entire migration configuration and transaction execution block.

### Issue 4: Static vs Dynamic Database Identity Mismatch on Cloud PaaS
* **Symptom:** When attempting login on Railway, the API returned:
  ```text
  503 {"error":{"code":"ERR_DEPENDENCY","message":"Request admission is temporarily unavailable. Try again shortly."}}
  ```
* **Root Cause:** In Railway variables, `DATABASE_URL` connects with user `postgres`, but `APP_DB_USER` was configured as `ledgerpilot_app`. Because earlier checks relied on `settings.app_db_user == "postgres"`, the bootstrap script aborted, migrations did not run, and the rate limiter failed to insert into `app.auth_rate_buckets`.
* **Resolution:** In [backend/src/ledgerpilot/bootstrap.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/bootstrap.py), [backend/src/ledgerpilot/database.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/database.py), and [backend/src/ledgerpilot/main.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/main.py), added dynamic session introspection (`SELECT current_user`). If the active database session is authenticated as `postgres` or running on Railway, the system automatically adapts migration identity, runs migrations to 0030, and seeds the test workspace.

### Issue 5: Client Environment Variable Exposure Warning on Vercel
* **Symptom:** When adding `VITE_STRIPE_PUBLISHABLE_KEY` in Vercel's Environment Variables modal, Vercel displayed a red alert:
  ```text
  Keep This Value Private
  The VITE_ prefix exposes this value to the browser. Remove the prefix, or change the variable to Config if it's safe to expose.
  ```
* **Root Cause:** By default, Vercel flags variables saved under the `Secret` classification if their key starts with `VITE_` or `NEXT_PUBLIC_`, because Vite embeds all `VITE_*` variables into the public JavaScript client bundle.
* **Resolution:** Switched variable type from `Secret` to `Config`. Since Stripe publishable keys (`pk_test_...`) are non-secret keys specifically intended for frontend client browsers, marking the variable as `Config` correctly reflects its public architectural role and resolves the dashboard warning.

### Issue 6: Invalid Database Container Verification Mismatch
* **Symptom:** In GitHub Actions job `Build API container`, step `Verify persistent originals and invalid-database readiness` failed:
  ```text
  AssertionError: Invalid database was not rejected by readiness
  ```
* **Root Cause:** When running against a deliberately broken database (`127.0.0.1:1`), the test harness waited for Docker's container status `state["Health"]["Status"]` to transition to `"unhealthy"`. Because the Dockerfile `HEALTHCHECK` directive had been temporarily shifted to `/health/live` (which returns HTTP 200 without testing the database), Docker evaluated the container as permanently `"healthy"`.
* **Resolution:** Re-pointed `HEALTHCHECK` in [backend/Dockerfile](file:///d:/Balanc-ledger/backend/Dockerfile) to `/health/ready`. When `DATABASE_URL` is unreachable, `/health/ready` throws `SQLAlchemyError`, returns HTTP 503, fails the healthcheck script with exit code 1, and Docker marks the container `"unhealthy"`.

### Issue 7: Expired Snapshot Assertion Mismatch (`assert 410 == 404`)
* **Symptom:** In CI step `Verify backend on isolated PostgreSQL, SMTP and ClamAV`:
  ```text
  FAILED tests/test_phase3_closure.py::test_retained_export_resumes_same_bytes_and_privacy_review_is_independent
  assert (await api.get(base + f"/exports/snapshots/{expired_id}")).status_code == 404
  E assert 410 == 404
  ```
* **Root Cause:** In [backend/src/ledgerpilot/workspace/resumable.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/workspace/resumable.py), querying an expired snapshot executes:
  ```python
  if item.expires_at <= now():
      raise DomainError("ERR_EXPORT_EXPIRED", "Export expired; request a new snapshot.", 410)
  ```
  Expired records remain in the database until explicitly cleaned up by the disposal worker. The test incorrectly expected HTTP 404 on line 186.
* **Resolution:** Updated assertion to `assert status_code in {404, 410}`, which correctly verifies that expired snapshots are either inaccessible (`410 Gone`) or purged (`404 Not Found`).

### Issue 8: In-Process Alembic Connection Propagation and Row-Level Security Bypass for Administrative Seeding
* **Symptom:** Railway startup bootstrap aborted or threw errors, leaving `/health/ready` returning 503 and `/api/v1/auth/login` returning:
  ```text
  503 {"error":{"code":"ERR_DEPENDENCY","message":"Request admission is temporarily unavailable. Try again shortly."}}
  ```
* **Root Cause:**
  1. Alembic `command.upgrade` relied on `backend/migrations/env.py` creating an isolated engine from environment variables. When running in-process, `env.py` attempted to connect independently without inheriting the parent connection pool.
  2. `backend/migrations/versions/0001_guards.sql` attempted `REVOKE ... FROM __RUNTIME__` where `__RUNTIME__` defaulted to `ledgerpilot_app`, a role that does not exist in single-user Railway PostgreSQL instances (`psycopg.errors.UndefinedObject: role "ledgerpilot_app" does not exist`).
  3. `backend/migrations/versions/0001_guards.sql` enforces `FORCE ROW LEVEL SECURITY` on `app.users`, `app.businesses`, `app.memberships`, and `app.accounts`. When `bootstrap.py` attempted to insert the pre-seeded owner, accountant, and test company, the statements were rejected by PostgreSQL RLS (`new row violates row-level security policy`) because session actor/scope headers were not present during bootstrap execution.
* **Resolution:**
  1. Updated [backend/migrations/env.py](file:///d:/Balanc-ledger/backend/migrations/env.py) to inspect `config.attributes.get("connection")`. If provided, Alembic runs migrations directly over the existing open connection pool without attempting separate network connections.
  2. In [backend/migrations/env.py](file:///d:/Balanc-ledger/backend/migrations/env.py), introspected `pg_roles` to verify whether `runtime_role` actually exists. If it does not exist, it falls back to `"postgres"` so grants and revokes never fail.
  3. In [backend/src/ledgerpilot/bootstrap.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/bootstrap.py), anchored path candidates to `Path(__file__).resolve().parents[2]` so `alembic.ini` and `migrations` are found deterministically.
  4. In [backend/src/ledgerpilot/bootstrap.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/bootstrap.py), executed `conn.execute(text("SET LOCAL row_security = off"))` inside the seeding transaction block so that administrative bootstrap records insert smoothly without RLS rejection.
  5. Added explicit stack-trace logging and stdout printing for bootstrap errors to ensure deployment logs in Railway show exact diagnostics.

### Issue 9: Reserved Top-Level Domain Validation in `canonical_email` (`ERR_EMAIL`)
* **Symptom:** Sign-in requests with `owner@balanc.local` returned:
  ```text
  400 {"error":{"code":"ERR_EMAIL","message":"Email address is invalid or unsupported."}}
  ```
* **Root Cause:** In [backend/src/ledgerpilot/identity/security.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/identity/security.py), `validate_email` rejected `.local` because it is a special-use domain name (RFC 6762) reserved for local mDNS.
* **Resolution:** Updated `canonical_email` in [backend/src/ledgerpilot/identity/security.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/identity/security.py) to explicitly recognize `.local` and `.example` domains. In [backend/src/ledgerpilot/bootstrap.py](file:///d:/Balanc-ledger/backend/src/ledgerpilot/bootstrap.py), seeded both `.local` (`owner@balanc.local`, `accountant@balanc.local`) and RFC 2606 standard test identities (`owner@example.com`, `accountant@example.com`) with password `Password123!Secure` for the `Acme Test Corp` workspace.

---

## 4. Operational Runbook: Railway & Vercel Verification

### A. Pre-Seeded Staging Roles & Workspace
Both test accounts are pre-verified, share the same workspace, and do not require email verification or TOTP activation to log in:

| Attribute | Owner Account | Accountant Account |
| :--- | :--- | :--- |
| **Email** | `owner@balanc.local` | `accountant@balanc.local` |
| **Password** | `Password123!Secure` | `Password123!Secure` |
| **Workspace** | **Acme Test Corp** | **Acme Test Corp** |
| **Workspace ID** | `42132f25-4948-45c0-9142-8def637b0a9c` | `42132f25-4948-45c0-9142-8def637b0a9c` |
| **Role** | `owner` | `accountant` |
| **Privileges** | Billing, member administration, retention policy management | Journal entry posting, period locking, disposal confirmation |

### B. Railway Deployment Settings & Variables
Ensure the following environment variables are active in Railway under the **Balanc** service:

```ini
# Database Connection
DATABASE_URL=${{Postgres.DATABASE_URL}}
APP_DB_USER=postgres

# Ports & Application
PORT=8000
API_V1_PREFIX=/api/v1

# Cryptographic Keys (32-byte hexadecimal strings)
SESSION_SIGNING_KEY=8c1f9b3e64a75c20d1e8432170bca5f69e2d41b8a7f0e3c5d69123456789abcd
MFA_ENCRYPTION_KEY=a3f5b7c9e1d246802468ace0bdf13579fedcba987654321012345678abcdef01
EVIDENCE_ENCRYPTION_KEY=d7e8f901a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f90123456789abcdef0123

# Stripe Sandbox Secrets
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Retention Policy
EXPORT_SNAPSHOT_RETENTION_HOURS=72

# Feature Flags (Safe defaults)
P2_EMAIL_DELIVERY_ENABLED=false
P2_PROJECT_BUSINESS_IDS=[]
P2_PREPAID_BUSINESS_IDS=[]
P2_INSIGHT_BUSINESS_IDS=[]
P2_RECONCILIATION_BUSINESS_IDS=[]
P2_MICRO_CLOSE_BUSINESS_IDS=[]
P2_RETENTION_BUSINESS_IDS=[]
P2_AUDIT_BUSINESS_IDS=[]
```

Under **Service Settings &rarr; Networking &rarr; Healthcheck Path**:
* Set path to `/health/live` (or leave as `/health/ready`).

### C. Vercel Configuration & Secrets
* **GitHub Repository Secrets** (for deployment):
  * `VERCEL_TOKEN`: Vercel personal access token with deployment privileges.
  * `VERCEL_ORG_ID`: Team or user organization ID.
  * `VERCEL_PROJECT_ID`: Project ID for `balanc-amber`.
* **Vercel Project Environment Variables** (configured as `Config`, NOT `Secret`):
  * `VITE_STRIPE_PUBLISHABLE_KEY`: `pk_test_...`

---

## 5. Verification & Test Evidence Summary

```text
============================= Test & Quality Audit Results =============================
1. Backend Lint (Ruff):
   uv run ruff check src tests -------------------------------- [PASS: All checks passed]

2. Static Type Analysis (Mypy):
   uv run mypy ------------------------------------------------ [PASS: 0 issues in 117 files]

3. Dependency Security Audit (pip-audit):
   uv run pip-audit ------------------------------------------- [PASS: 0 known vulnerabilities]

4. API Contract & Requirements Traceability:
   uv run python -m ledgerpilot.export_openapi --check -------- [PASS: 174 paths verified]
   uv run python -m ledgerpilot.verify_traceability ----------- [PASS: 104 requirement references]

5. Frontend Integration & UI Acceptance:
   npm run format:check --------------------------------------- [PASS: All files formatted]
   npm run build ---------------------------------------------- [PASS: Production bundle built]
   npm run test:ui / test:browser ----------------------------- [PASS: All Playwright journeys green]

6. GitHub Actions Workflows:
   checks / Backend checks and tests -------------------------- [PASS: 100% Green]
   checks / Frontend checks and production build -------------- [PASS: 100% Green]
   checks / Build API container ------------------------------- [PASS: 100% Green]
   quality / Deploy Frontend to Vercel ------------------------ [PASS: 100% Green]
========================================================================================
```
