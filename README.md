# LedgerPilot

LedgerPilot is a responsive web finance office for small businesses. The first release is web-only: a modular FastAPI backend, React/TypeScript/Vite frontend, and PostgreSQL. No native mobile application is included.

Backend: [foundation](docs/phase-2-backend-foundation.md), [business modules](docs/phase-3-business-modules.md) and [local verification](docs/phase-4-backend-verification.md). Web implementation and acceptance: [Phase 5](docs/phase-5-responsive-frontend.md). Governing status: [implementation tracker](docs/Implementation_Plan.md).

## Prerequisites

- Git
- Python 3.12 and `uv`
- Node.js 22 and npm (the repository includes `.nvmrc`)
- Docker Desktop with Docker Compose for local PostgreSQL and container builds

## Fresh local setup

1. Install locked backend dependencies and prepare local credentials. The helper generates missing random secrets, aligns runtime/migration URLs, preserves unrelated settings and prints only variable names:

   ```powershell
   cd backend
   uv sync --locked --all-groups
   uv run python -m ledgerpilot.local_setup prepare
   cd ..
   ```

   If a pre-existing secret is too short, preparation stops without changing the file. Before the first database start, use `prepare --replace-weak-local-secrets` to replace undersized values. After a database exists, follow the rotation procedure in [Phase 1 setup](docs/phase-1-service-setup.md). Never reuse local credentials in a hosted environment.

2. Start PostgreSQL: `docker compose up -d --wait --wait-timeout 90 postgres`. A fresh volume initializes separate runtime/migration roles. For an existing volume, run `./scripts/bootstrap-local-db.ps1` to apply current local role settings without erasing data. If port 5432 is occupied, run `uv run python -m ledgerpilot.local_setup prepare --port 15432` from `backend`, then rerun Compose. It updates `POSTGRES_PORT` and both URLs together. This workstation uses port 15432 after Windows rejected the previous binding; the template defaults to 5432.

3. From `backend`, run `uv run python -m ledgerpilot.local_setup verify`. To test password rotation, stop API/workers first and use `verify --check-rotation`; this temporarily changes and restores the runtime password. Apply migrations with `uv run alembic upgrade head`, then start the API with `uv run uvicorn ledgerpilot.main:app --reload`.

4. In a second terminal, install and start the web client:

   ```powershell
   cd frontend
   npm ci
   npm run dev
   ```

5. Open the Vite URL shown in the terminal. API docs are at `http://localhost:8000/docs`; the liveness endpoint is `http://localhost:8000/health/live`.

The API's `DATABASE_URL` must use the restricted runtime role. Use `MIGRATION_DATABASE_URL` only for Alembic/operator work; do not configure the running API with the migration credential. To erase and recreate the disposable local PostgreSQL database, run `./scripts/reset-local-db.ps1 -ConfirmReset` from PowerShell. This deletes the local Compose volume and all of its data. Real-service tests seed disposable databases; development books are not automatically populated with sample financial entries.

## Local verification

Run these commands before a pull request:

```powershell
cd backend
uv sync --locked --all-groups
uv run ruff check src tests
uv run mypy
uv run coverage run -m pytest -q
uv run coverage report
uv run python -m compileall -q src
cd ..\frontend
npm ci
npm run build
cd ..
docker build -f backend/Dockerfile -t ledgerpilot-api:local backend
```

The backend CI job also generates isolated credentials, starts real PostgreSQL, verifies restricted roles and password rotation, and removes its disposable CI volume. Unit tests use explicit synthetic settings; database checks do not require paid provider credentials. Database-backed integration tests must use a local/test database, never production data.

## Repository boundaries

- `backend/src/ledgerpilot/`: FastAPI modules; routers handle HTTP, services handle workflows/accounting rules, and repositories own persistence.
- `frontend/`: responsive React/TypeScript/Vite client. The browser never connects directly to PostgreSQL or receives private credentials.
- `docs/`: SRS, governing implementation plan, standards, and architecture decisions.
- `deploy/`: demo and future production deployment assets.
- `.github/`: CI/CD workflows and SRS-linked issue/pull request templates.

## CI and deployments

GitHub Actions validates pull requests and pushes with locked backend dependencies, Ruff, mypy, pytest/coverage, Python compilation, a frontend production build, and an API container build. The AWS demo deploy job runs from the configured deployment branch only after all required CI jobs pass and the repository variable `ENABLE_DEMO_DEPLOY=true` enables deployment. Required AWS values are validated inside the protected `demo` environment. It uses GitHub OIDC, Amazon ECR, and AWS Systems Manager rather than stored long-lived AWS access keys.

Connect the repository to Vercel for web previews and production deploys. Set project root to `frontend`, build command to `npm run build`, output directory to `dist`, and production branch to the selected GitHub default branch. Protect that branch and require the `Backend checks and tests`, `Frontend checks and production build`, and `Build API container` checks before merge. The local repository uses `main`; align Vercel, AWS role trust, deployment settings and branch protection with the selected remote default branch.

### AWS demo setup (one time)

The AWS workflow deploys to an EC2 demo host; it does not provision AWS resources. Create an ECR repository and an EC2 instance registered as a Systems Manager managed node. Attach an instance role that permits ECR image pulls and SSM management. Install Docker and AWS CLI, create `/etc/ledgerpilot/api.env` with the API configuration, set root ownership and mode `600`, and install `deploy/aws/deploy-api.sh` as `/opt/ledgerpilot/deploy-api.sh` with executable permissions. Configure a TLS reverse proxy and DNS for the API hostname, keep port 8000 private, and allow HTTPS from the public internet. Set `FRONTEND_ORIGINS` to the Vercel production origin.

Configure GitHub Actions OIDC and an IAM role whose trust policy is limited to this repository and the protected `demo` GitHub environment. Restrict its permissions to pushing to the target ECR repository and sending `AWS-RunShellScript` to the one EC2 instance. Add these environment variables to the GitHub `demo` environment:

- `AWS_ROLE_ARN`: deployment role ARN.
- `AWS_REGION`: region containing ECR and EC2.
- `AWS_ACCOUNT_ID`: AWS account number.
- `ECR_REPOSITORY`: ECR repository name.
- `EC2_INSTANCE_ID`: demo instance ID.

After setup, set the repository-level variable `ENABLE_DEMO_DEPLOY=true`. Deployment is restricted to the repository default branch and active runs are not automatically cancelled. CI saves the built API image with one-day artifact retention; deployment and release load this exact image rather than rebuild it. Re-run the full workflow if approval outlasts artifact retention. Monitor Actions storage/minute quotas.

After setup, each successful push to the configured deployment branch publishes an immutable commit-SHA image to ECR and asks SSM to deploy it. The instance checks API health and restores the previous image if the new deployment fails. A version tag such as `v0.1.0` also publishes an API image to GitHub Container Registry.

The EC2 path is a quota-limited demo, not the production AWS topology in the SRS. Production still requires the planned load balancing, managed database, private networking, backups, monitoring, and operations budget. Verify AWS eligibility and quotas before provisioning; free-tier terms can change. Do not use the demo for real customer books, money movement, or tax filing.

## Accounting and security invariants

Posted journals must be balanced, immutable, idempotent, auditable, and scoped to the active business. Period locks and subledger/control-account tie-outs are enforced by backend services and PostgreSQL. Bank transactions are evidence of cash movement; uncertain transactions remain in review rather than being silently posted as income or expense. The SRS is the source of requirements; record architecture changes as ADRs and update the SRS before implementation.

## Local identity mailbox

Mailpit's browser port is configured with `MAILPIT_UI_PORT` (default `8025`); this sets both the Compose host port and the local mailbox link. It does not change `SMTP_PORT` (default `1025`). Pending signup survives a temporary SMTP outage; use the original registration browser to resend when delivery recovers. No automatic email queue is configured. `RATE_WINDOW_SECONDS` and `RATE_COOLDOWN_MAX_SECONDS` default to `60` and support values from 1 to 3600; defaults need no `.env` changes. See [the QA resolution record](docs/qa-findings-resolution.md).

Start `docker compose -f docker-compose.auth.yml up -d` from the repository root and enable `AUTH_DELIVERY_ENABLED=true` in the private environment. Mailpit captures real local verification/reset messages at `http://127.0.0.1:8025`. Keep the generated MFA encryption key backed up privately. Browser authentication requires HTTPS and an explicitly allowed origin; see the Phase 2 record for Secure-cookie and CSRF setup.

## Local document scanning and OCR

Run `uv run python -m ledgerpilot.local_setup prepare` from `backend` to prepare missing local secrets without displaying them. Preserve the evidence encryption and export signing keys in private backup; replacing them can make originals unreadable or break signing continuity. Set `OCR_ENABLED=true` in the ignored root environment, then run `docker compose -f docker-compose.documents.yml up -d --build --wait --wait-timeout 300` from the root. ClamAV and the Tesseract/Poppler parser are local services without a paid API. They require downloads, local disk and RAM; their configured memory ceilings total 6 GiB.

Apply `uv run alembic upgrade head` from `backend`. Uploads remain encrypted and quarantined until a scoped worker successfully scans them. Run one job with `uv run python -m ledgerpilot.documents.worker --actor USER_UUID --business BUSINESS_UUID`. This command uses an existing authorized member, not a public impersonation endpoint. Supported extraction formats, manual fallbacks, resource limits and worker recovery are in the [Phase 3 record](docs/phase-3-business-modules.md) and [extraction contract](docs/contracts/document-extraction.md). No scanning or OCR result posts accounting entries automatically.

### Responsive web development and acceptance

The web implementation and evidence are recorded in [Phase 5](docs/phase-5-responsive-frontend.md). Use `http://127.0.0.1:5173` consistently with `FRONTEND_ORIGINS`; Vite proxies `/api` to `127.0.0.1:8000`. Start the API and the approved scoped evidence worker for upload processing. The frontend never needs database credentials or signing keys.

From `frontend`, run `npm run test:ui`, `npm run test:api` and `npm run test:live`. The last command needs PostgreSQL/Mailpit/ClamAV and an unused local port 8000; it creates and removes an isolated synthetic database. On Linux set `LEDGERPILOT_TEST_PYTHON` to the backend virtual environment's Python. Results are written to ignored `tmp/phase5/live-results.json`; CI retains them as an artifact.

For Vercel, configure `VITE_API_ORIGIN` to the approved HTTPS API origin before building; the checked-in SPA rewrite supports deep links. Hosted cookie/CORS behavior still needs the Phase 9 deployment checks. Prefer web/API hostnames under the same approved site; a cross-site arrangement needs explicit SameSite/credentialed-CORS and browser verification. No hosted deployment is performed by these local checks.

### Phase 6 UI acceptance

From `frontend`, run `npm ci`, `npx playwright install chromium`, then `npm run test:phase6`. This builds the production frontend and tests it on a dedicated loopback preview server at port 4176 using isolated synthetic API responses. It does not require the database or provider keys. Browser evidence is written to `tmp/phase6/`; failure traces/screenshots are under `frontend/test-results/`. CI runs the browser gate and uploads evidence. See [the Phase 6 record](docs/phase-6-ui-testing.md) for scope and Phase 10 deferrals.

### Phase 7 real API integration

`npm run integration:check` in frontend verifies the recorded OpenAPI call-site inventory. With local PostgreSQL, Mailpit and ClamAV healthy and port 8000 unused, `npm run test:phase7` runs contracts, UI/transport/build/browser checks and isolated real-service journeys. Set LEDGERPILOT_TEST_PYTHON on Linux. Only VITE_API_ORIGIN belongs in frontend configuration; it must contain an origin without credentials, path, query or fragment. See [the integration record](docs/phase-7-api-integration.md) for environment and hosted-cookie boundaries.

### Phase 8 independent integrated acceptance

With the local PostgreSQL/Mailpit/ClamAV/OCR services healthy, run the full backend pytest suite with the four service flags described in [Phase 8](docs/phase-8-integration-testing.md). From frontend, `npm run test:phase8` runs contract, build, UI/browser and real-service integration checks. `npm run test:browser:live` alone creates a disposable API/database fixture and drives the built frontend in Chromium on port 4178; port 8000 must be free. Install dependencies and Chromium first. After successful checks, run `node scripts/record-phase8-candidate.mjs` from the repository root to identify the tested source candidate. Secrets and raw local fixtures are excluded from the candidate manifest.

### Password, cookie and CSRF transport boundary

Hosted profiles reject non-HTTPS API requests before reading credential bodies, except GET/HEAD health probes, and issue HSTS on HTTPS responses. TLS must terminate at the trusted proxy; keep API port 8000 private and configure Uvicorn FORWARDED_ALLOW_IPS with only the actual proxy peers. Do not trust arbitrary X-Forwarded-Proto headers or set a public wildcard. Validate proxy forwarding during Phase 9/10. The browser transport permits HTTP only for same-origin loopback development. HttpOnly protects cookies from scripts; browser-owner DevTools can still inspect cookies and request payloads. See [the focused security review](docs/password-session-csrf-security.md).
