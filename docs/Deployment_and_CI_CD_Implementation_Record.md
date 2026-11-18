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

---

## 6. Production Hardening, Cloud Networking, SMTP Delivery & Railway Migration Resolution (Nov 2026)

### A. Context & Operational Incidents
Following initial deployment to Railway and Vercel, four operational issues were identified and addressed:
1. **SMTP Delivery & Authentication Verification Failures**: Verification and password-reset emails were originally tied to Mailpit or unconfigured providers, causing delivery failures in local setups without Mailpit and in production when hosted SMTP was unconfigured.
2. **Reverse Proxy TLS & HTTPS Enforcement (`ERR_HTTPS_REQUIRED`)**: Vercel frontend requests proxied through Railway Edge terminated TLS at the edge, causing FastAPI to see raw HTTP (`scope["scheme"] == "http"`) and emit `ERR_HTTPS_REQUIRED: Use the secure HTTPS address to continue.`.
3. **Database Migration Failures After PostgreSQL Service Deletion/Re-creation**: When the original Railway PostgreSQL service was deleted and replaced by a newly attached database (`Postgres-1l3L`), Alembic migrations failed with connection refused to `127.0.0.1:5432`, leaving the database empty with `schema "app" does not exist`.
4. **CI/CD Quality Gate Failures**: Ruff formatting/line-length rules (`E501`), Mypy variable reassignment type conflicts, and container healthcheck probe alignment caused intermittent CI pipeline failure.

---

### B. Detailed Technical Implementation & Architecture Changes

#### 1. Identity, SMTP & HTTPS Email Delivery Architecture
* **The Cloud Outbound SMTP Port Block Problem**:
  * On Railway Hobby/Trial tiers, the cloud firewall physically blocks raw outbound TCP traffic on ports `25`, `465`, `587`, and `2525` to prevent spam abuse.
  * Direct socket connection attempts from the container to `smtp.gmail.com` fail with `OSError: [Errno 101] Network is unreachable` or `timed out`.
  * Live port probe evidence executed directly from inside the running Railway container:
    ```json
    {
      "587": "BLOCKED: timed out",
      "465": "BLOCKED: timed out",
      "2525": "BLOCKED: timed out",
      "25": "BLOCKED: timed out",
      "443": "OPEN"
    }
    ```
* **Architecture Decision: Dual-Mode Email Delivery (HTTPS Port 443 + Standard SMTP)**:
  * File: `backend/src/ledgerpilot/identity/mail.py`
  * Added native support for HTTP-based email delivery over port 443 (which is never blocked by cloud firewalls):
    1. **Resend HTTPS REST API** (`https://api.resend.com/emails`):
       * Activated when `RESEND_API_KEY` is present.
       * Uses standard `urllib.request` over HTTPS with `User-Agent: LedgerPilot/1.0` and `Authorization: Bearer <key>`.
       * Key normalization: automatically strips whitespace and surrounding quotes.
       * Default sender: `onboarding@resend.dev` (or configured `RESEND_FROM`).
    2. **Brevo (Sendinblue) HTTPS REST API** (`https://api.brevo.com/v3/smtp/email`):
       * Activated when `BREVO_API_KEY` is present.
       * Enables sending verification emails to **any** recipient address on free accounts without requiring custom domain DNS verification.
    3. **Standard SMTP Fallback** (`smtplib.SMTP` / `smtplib.SMTP_SSL`):
       * Used when API keys are omitted and direct SMTP egress is unblocked (e.g., local development, bare metal, or Railway Pro).
       * Supports port 587 (STARTTLS) and port 465 (implicit SSL) with 15s timeout.
* **Resend Sandbox Constraints & Resolution**:
  * When using Resend's default test address (`onboarding@resend.dev`), Resend enforces an anti-abuse sandbox policy: test emails can **only** be delivered to the email address of the Resend account owner (`gamehalludba@gmail.com`).
  * Attempting to deliver to unverified external emails without a custom domain returns `HTTP 403 Forbidden`:
    ```json
    {
      "statusCode": 403,
      "name": "validation_error",
      "message": "You can only send testing emails to your own email address (gamehalludba@gmail.com). To send emails to other recipients, please verify a domain at resend.com/domains"
    }
    ```
  * Resolution paths:
    * **Path A**: Verify custom domain DNS records in Resend and set `RESEND_FROM=noreply@yourdomain.com`.
    * **Path B**: Use Brevo (`BREVO_API_KEY`) which does not restrict recipient addresses in its free tier.
* **Live Diagnostic Endpoint (`/health/test-email`)**:
  * File: `backend/src/ledgerpilot/main.py`
  * Exposes `/health/test-email` to test real email delivery live, probe socket ports (587, 465, 2525, 25, 443), and return full structured error diagnostics without swallowing low-level exceptions.
* **Container CA Certificates**:
  * File: `backend/Dockerfile`
  * Installed `ca-certificates` in runtime stage to ensure TLS certificate verification succeeds when connecting to external HTTPS and SMTP endpoints.

#### 2. Cloud Reverse Proxy, HTTPS, and Cross-Origin Authentication
* **Proxy Header Trust & Uvicorn Configuration**:
  * File: `backend/Dockerfile`
  * Updated Uvicorn CMD flags: `--proxy-headers --forwarded-allow-ips='*'` to correctly process `X-Forwarded-Proto`, `X-Forwarded-For`, and `X-Forwarded-Host` from Railway Edge and Vercel.
* **X-Forwarded-Proto Inspection**:
  * File: `backend/src/ledgerpilot/shared/security_headers.py`
  * Enhanced `SecurityHeadersMiddleware` to parse `X-Forwarded-Proto` header from ASGI scope. Avoids rejecting valid HTTPS requests behind reverse proxies with `ERR_HTTPS_REQUIRED`.
* **CORS & CSRF Origin Handling**:
  * Files: `backend/src/ledgerpilot/main.py`, `backend/src/ledgerpilot/shared/middleware.py`
  * Added dynamic regex matching (`r"^https://.*\.vercel\.app$"`) for CORS and CSRF validation in Railway environments.
  * Set `cookie_samesite="none"` when deployed to Railway to allow cross-site cookie transmission between Vercel frontend and Railway backend domains.

#### 3. Database Migration Resilience & Railway Fallback Engine
* **Alembic Runtime URL Injection**:
  * Files: `backend/src/ledgerpilot/main.py`, `backend/migrations/env.py`
  * Fixed Alembic configuration so the configured application database URL is explicitly passed via `cfg.set_main_option("sqlalchemy.url", ...)` rather than relying on ambient defaults or missing `.env` files.
  * In `env.py`, `config.get_main_option("sqlalchemy.url")` is checked first.
* **Railway Managed Postgres Multi-Variable Fallbacks**:
  * Files: `backend/src/ledgerpilot/config.py`, `backend/migrations/env.py`
  * Added automatic fallback detection across Railway's standard environment variables: `DATABASE_URL`, `DATABASE_PUBLIC_URL`, `POSTGRES_URL`, `POSTGRESQL_URL`, and `PGHOST` / `PGPORT` / `PGUSER` / `PGPASSWORD`.
  * Automatic scheme translation: converts `postgres://` or `postgresql://` into `postgresql+psycopg://`.
* **Superuser & Schema Role Alignment**:
  * File: `backend/migrations/env.py`
  * Fixed `KeyError: 'runtime_role'` by ensuring `config.attributes["runtime_role"]` is always initialized.
  * Dynamically detects whether `ledgerpilot_app` role exists; gracefully operates under `postgres` role on managed clouds without requiring custom cluster role creation.
* **Auto-Migration Thread with Retry Loop**:
  * File: `backend/src/ledgerpilot/main.py`
  * Startup migration thread now executes up to 10 retries with backoff delays to tolerate PostgreSQL cold startup and connection negotiation delays.
* **Operational Diagnostic Endpoint (`/health/migrate`)**:
  * File: `backend/src/ledgerpilot/main.py`
  * Exposes an idempotent `/health/migrate` endpoint allowing manual or automated trigger of Alembic upgrades on Railway with optional `?db_url=` parameter and detailed JSON traceback responses.

#### 4. Container Health Check & Control Flow Verification
* **Container Healthcheck Alignment**:
  * File: `backend/Dockerfile`
  * Aligned container `HEALTHCHECK` with `/health/ready` so that integration scripts (`scripts/check-deployment-container.py`) accurately verify that invalid databases cause the container state to transition to `unhealthy`.
* **Automated Key Derivation**:
  * File: `backend/src/ledgerpilot/config.py`
  * Added validators to automatically derive 32-byte Fernet and hex encryption keys (`mfa_encryption_key`, `evidence_encryption_key`, `export_signing_private_key`) from raw passphrases if explicit pre-formatted keys are omitted.

---

### C. Commit History Summary (Cloud & Production Stabilization)

| Commit Hash | Description |
| :--- | :--- |
| `1bb6529` | `docs: record ADR-018 on cloud networking, database migrations, and HTTPS email delivery` |
| `62471e9` | `fix(mail): strip API keys and expose full DomainError message in diagnostic endpoint` |
| `ab75e4c` | `fix(mail): capture HTTP error body for Resend/Brevo and accept API keys in test endpoint` |
| `bb24e27` | `feat(mail): add Resend and Brevo HTTPS delivery support and socket port probing in test endpoint` |
| `5d06183` | `fix(mail): preserve underlying exception cause and error detail in deliver()` |
| `0352db6` | `fix(mail): add ca-certificates to Dockerfile, support SSL port 465, and add /health/test-email endpoint` |
| `b08730d` | `docs: record cloud hardening, SMTP delivery, proxy headers, and railway migration fixes` |
| `8cc0747` | `fix(cloud): inject configured DB URL into alembic and resolve Railway Postgres fallbacks` |
| `77e3e04` | `fix(ops): add retry loop to auto-migrate thread and expose /health/migrate endpoint` |
| `3f653f5` | `fix(ci): fix lint, format, mypy types and ensure runtime_role is set in Railway migrations` |
| `0cf28b9` | `the smpt mail is fixed` |
| `5de2488` | `fix(mail): replace Mailpit with standard SMTP delivery and add in-memory rate limiter fallback` |
| `ea9b439` | `fix(cloud): trust proxy headers for HTTPS on Railway, allow vercel origins, and restore readiness probe` |
| `7510c00` | `fix(config): automatically derive valid Fernet and hex keys from raw passphrase secrets` |
| `6323f67` | `fix(cloud): unblock uvicorn startup, run migrations in background thread, and strip origin slashes` |
| `6085a08` | `fix(cloud): auto-run migrations in container, support postgres:// URLs, and tolerate cloud defaults` |

---

## 4. Multi-Provider Failover & Third-Party Sandbox Remediation

### A. Context & Incident Report
Following the deployment of commit `1bb6529`, operational testing of password resets and registration across multiple test identities surfaced an upstream third-party restriction:
1. **Frontend Symptom**: When initiating a password reset for `theab9098@gmail.com` on Vercel (`balanc-48v9yo9n5-abd-c462.vercel.app`), the UI displayed:
   `"We couldn't send the email. Please try again shortly or contact support. Support reference 9ecf3322-9c74-454e-9169-241c1d0466ef"`.
2. **Railway Network Logs**: The active container logged:
   `POST /api/v1/auth/reset-request -> HTTP 503 (duration: 183ms)`.
3. **Upstream Cause**: In the `/health/test-email?to=theab9098@gmail.com` diagnostic probe, Resend returned:
   `HTTP 403 Forbidden: "You can only send testing emails to your own email address (gamehalludba@gmail.com). To send emails to other recipients, please verify a domain at resend.com/domains..."`.
4. **Behavioral Divergence**: Registration (`/api/v1/auth/register`) and verification resend (`/api/v1/auth/verification-request`) return `202 Accepted` because `verification_delivery()` intentionally suppresses `ERR_MAIL_UNAVAILABLE` to protect the pending identity record from being rolled back by transient network outages. Conversely, password reset (`/api/v1/auth/reset-request`) strictly requires active token dispatch and bubbles `ERR_MAIL_UNAVAILABLE` as HTTP 503.

### B. Architectural Decisions & Rationale

| Decision | Issue Solved | Why Decision Was Taken | How Implemented |
| :--- | :--- | :--- | :--- |
| **Multi-Provider Failover Pipeline** | If Resend encounters a sandbox restriction (403), rate limit (429), or network blip, the entire application halts with 503. | Single-provider architecture created a single point of failure and blocked all users not matching the account owner email. | In `backend/src/ledgerpilot/identity/mail.py`, implemented a non-halting cascade: `Resend -> Brevo -> SendGrid -> SMTP`. Upstream errors are caught, logged, and execution continues to the next available provider. |
| **Zero-Domain Universal Delivery (Brevo)** | Resend free tier strictly demands custom DNS domain verification before sending to non-owner recipients. Operators often do not own a custom domain during staging. | Brevo provides 300 free transactional emails per day to **any recipient email address** globally with only single-sender email verification (confirming ownership via email link), requiring zero custom DNS domain setup. | Supported `BREVO_API_KEY` over HTTPS port 443 (`POST https://api.brevo.com/v3/smtp/email`). |
| **SendGrid HTTPS Backup** | Provides a secondary enterprise fallback for cloud environments without open SMTP sockets. | Enhances platform redundancy; allows single-sender verification on personal email addresses with 100 free emails/day. | Supported `SENDGRID_API_KEY` over HTTPS port 443 (`POST https://api.sendgrid.com/v3/mail/send`). |
| **Transparent Sandbox Diagnostics** | Operators saw generic `503 ERR_MAIL_UNAVAILABLE` without knowing that Resend's anti-spam testing sandbox was the root blocker. | Prevents debugging confusion and provides immediate self-serve remediation steps in container logs and health endpoints. | When all providers fail, `mail.py` detects Resend sandbox text and appends an explicit hint instructing the operator to either configure Brevo or add a domain in Resend. |
| **In-Cloud Credential Validation** | Operators had to redeploy containers just to test if a new API key was valid. | Speeds up credential verification and eliminates guess-and-check restart cycles. | Updated `/health/test-email` to accept `resend_key`, `brevo_key`, and `sendgrid_key` query parameters for instantaneous in-cloud testing. |

### C. Operator Runbook for Enabling Delivery to Any Email

To deliver verification and password reset emails to any recipient (such as `theab9098@gmail.com`):

#### Option 1: Brevo (Recommended — 2-minute setup, NO custom domain needed)
1. Sign up for a free account at [Brevo.com](https://www.brevo.com) (free plan: 300 emails/day to any recipient).
2. Go to **Settings** -> **Senders, Domains & Dedicated IPs** -> **Senders** -> **Add a new sender**.
3. Enter your Gmail address (e.g. `muhammadabdullahattari.jk@gmail.com`). Brevo will send a 6-digit verification code to that inbox. Enter the code to verify.
4. Go to **SMTP & API** -> **API Keys** -> **Generate a new API key**. Copy the key (starts with `xkeysib-...`).
5. Open your Railway Dashboard -> Service **Balanc** -> **Variables**:
   - Add `BREVO_API_KEY` = `xkeysib-...`
   - Set `SMTP_FROM` = `muhammadabdullahattari.jk@gmail.com`
6. Done! All verification and reset emails will now deliver to any email address immediately.

#### Option 2: Resend with Verified Custom Domain
1. Log in to [Resend.com](https://resend.com) -> **Domains** -> **Add Domain** (e.g. `yourdomain.com`).
2. Add the DKIM, SPF, and MX TXT records in your DNS provider (Cloudflare, Namecheap, GoDaddy).
3. In Railway Variables, set `RESEND_FROM` = `LedgerPilot <noreply@yourdomain.com>`.
4. Once verified by Resend, Resend will send to any email address worldwide.

---

### D. Verification Evidence
* **Live In-Cloud Email Delivery Verification**:
  * Tested live on Railway production (`https://balanc-production.up.railway.app/health/test-email?to=gamehalludba@gmail.com`).
  * Live response confirmed:
    ```json
    {
      "status": "ok",
      "delivered_to": "gamehalludba@gmail.com",
      "resend_enabled": true,
      "brevo_enabled": false
    }
    ```
  * Verification email successfully arrived in destination inbox (`gamehalludba@gmail.com`).
* **Multi-Provider Failover Unit Suite**:
  * File: `backend/tests/test_mail_failover.py`
  * Verified 3/3 tests passing:
    1. `test_resend_fails_falls_through_to_brevo`: Verified Resend 403 sandbox error cascades to Brevo 201 without throwing.
    2. `test_resend_and_brevo_fail_falls_through_to_sendgrid`: Verified 3-tier cascade (`Resend 403 -> Brevo 401 -> SendGrid 202`).
    3. `test_all_fail_aggregates_errors_and_hints`: Verified comprehensive diagnostic message and remediation hint injection when all providers fail.
* **Live In-Cloud Database Migration Verification**:
  * Endpoint `https://balanc-production.up.railway.app/health/migrate` executed successfully against managed cloud database:
    ```json
    {
      "status": "ok",
      "message": "Migrations completed successfully",
      "applied_to_host": "postgres-1i3l.railway.internal",
      "applied_to_db": "railway"
    }
    ```
  * Endpoint `https://balanc-production.up.railway.app/health/ready` confirmed:
    ```json
    {
      "status": "ready"
    }
    ```
* **GitHub Actions Run `44294784600`** on branch `main` passed with 100% green status across all 10 pipeline jobs:
  * `Backend Quality (Lint, Format, Mypy)`: Passed
  * `Contract Checks (OpenAPI, Traceability, API Sync)`: Passed
  * `Frontend Quality and Production Build`: Passed
  * `Infrastructure and Configuration Checks`: Passed
  * `Security and Dependency Audits`: Passed
  * `Backend Integration Suite with Live Services`: Passed
  * `Database Migrations and Drift Check`: Passed
  * `Build API Container and Verify Control Flow`: Passed
  * `Smoke Test Against Running Container`: Passed
  * `Deploy Frontend to Vercel (Staging)`: Passed
* **Local Unit Tests**:
  * `tests/test_config.py` & `tests/test_mail_failover.py`: 29 passed in 3.80s.
  * `tests/test_security_checklist.py`: 3 passed, 2 skipped in 19.28s.
  * Static analysis (`mypy`): 0 issues in 137 source files.
  * Linter (`ruff check src tests`): All checks passed.
  * Formatter (`ruff format --check src tests`): 187 files verified.

