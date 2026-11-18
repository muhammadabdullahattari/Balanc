# ADR-018: cloud networking, managed database migrations, and HTTPS email delivery

Status: Accepted, November 2026.

## Context

During cloud staging deployment of LedgerPilot to Railway (API and PostgreSQL) and Vercel (frontend), several infrastructure boundaries and runtime edge cases emerged:

1. **Managed Database Orchestration & Service Lifecycle**:
   When cloud PostgreSQL instances are re-created or re-attached under different service identifiers (e.g., `Postgres-1l3L`), ambient `.env` files are not present. Alembic migrations and the runtime application engine originally defaulted to `localhost:5432`, resulting in `(psycopg.OperationalError) connection to server at "127.0.0.1", port 5432 failed: Connection refused`, leaving the database empty with `schema "app" does not exist`. Furthermore, managed PostgreSQL clusters utilize a single administrative role (`postgres`) rather than pre-provisioned cluster roles (`ledgerpilot_app`, `ledgerpilot_migrator`), causing role-assertion crashes (`KeyError: 'runtime_role'`).

2. **Cloud Reverse Proxy Header & TLS Termination**:
   Railway Edge and Vercel terminate TLS at their respective network perimeters, forwarding requests to the container over cleartext HTTP. Because `SecurityHeadersMiddleware` originally inspected only raw ASGI `scope["scheme"]`, valid HTTPS requests through the reverse proxy were rejected with `403 ERR_HTTPS_REQUIRED: Use the secure HTTPS address to continue.`.

3. **Cloud Egress SMTP Port Blocking**:
   Cloud hosting providers (including Railway on Trial/Hobby tiers) physically block outbound raw TCP traffic on standard SMTP ports (`25`, `465`, `587`, `2525`) at the cloud firewall level to mitigate spam abuse. Outbound socket connection attempts to `smtp.gmail.com` raised `OSError: [Errno 101] Network is unreachable` and connection timeouts, silently aborting account verification email dispatch.

4. **Testing Provider Sandboxing**:
   Third-party email delivery services (e.g., Resend free tier) restrict default testing domains (`onboarding@resend.dev`) strictly to the registered account owner's email address, returning `HTTP 403 Forbidden` when attempting delivery to arbitrary test identities without a verified custom domain.

---

## Decisions

### 1. Dynamic Alembic Runtime Injection and Cloud Multi-Variable Fallbacks
* **Alembic URL Injection**: Rather than relying on ambient configuration or config file defaults, `main.py` explicitly injects the active, validated database URL into Alembic's runtime options via `cfg.set_main_option("sqlalchemy.url", configured_db_url)`.
* **Multi-Variable Fallback Detection**: In `config.py` and `migrations/env.py`, if the primary `DATABASE_URL` is omitted or points to ambient local defaults on cloud platforms, the system automatically evaluates fallback environment variables in order: `DATABASE_PUBLIC_URL`, `POSTGRES_URL`, `POSTGRESQL_URL`, and standard component variables (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`).
* **Dialect Normalization**: Cloud URLs beginning with `postgres://` or `postgresql://` are automatically translated to `postgresql+psycopg://` to satisfy strict driver requirements.
* **Superuser Role Tolerance**: On cloud environments where custom cluster roles cannot be pre-provisioned, `migrations/env.py` dynamically verifies whether `ledgerpilot_app` exists and gracefully falls back to `postgres` superuser execution, ensuring `config.attributes["runtime_role"]` is always initialized.
* **Cold-Start Auto-Migration**: Startup migrations run in an asynchronous background thread with an exponential backoff loop (up to 10 attempts) to tolerate database startup delays during cold container boots.
* **On-Demand Migration Endpoint**: An idempotent diagnostic endpoint `/health/migrate` is exposed to allow operators to trigger and verify migrations on demand, returning structured execution status and target host metadata.

### 2. Edge Reverse Proxy Header Trust & Cross-Origin Security
* **Uvicorn Proxy Headers**: The container start command is configured with `--proxy-headers --forwarded-allow-ips='*'` to correctly process `X-Forwarded-Proto`, `X-Forwarded-For`, and `X-Forwarded-Host`.
* **X-Forwarded-Proto Inspection**: `SecurityHeadersMiddleware` inspects `X-Forwarded-Proto` before evaluating TLS enforcement, preventing false-positive HTTPS rejections behind edge proxies.
* **Dynamic Vercel/Railway Origin Allowance**: CORS and CSRF validation allow dynamic origins matching `r"^https://.*\.vercel\.app$"` and `r"^https://.*\.railway\.app$"` when running in cloud environments.
* **Cross-Site Cookie Policy**: `cookie_samesite` dynamically defaults to `none` (with `secure=True`) on cloud deployments to permit cross-site authentication cookie transmission between Vercel frontends and Railway backends.

### 3. Dual-Mode Email Delivery Architecture (HTTPS Port 443 + SMTP)
* **Decoupling from Raw SMTP Sockets**: Identity email delivery in `backend/src/ledgerpilot/identity/mail.py` is decoupled from raw TCP SMTP sockets to eliminate reliance on unblocked outbound ports.
* **Native HTTPS Delivery (Port 443)**:
  * **Resend Integration**: When `RESEND_API_KEY` is present, emails are dispatched via Resend's HTTPS REST API (`https://api.resend.com/emails`) using standard `urllib.request` over port 443 (never blocked by cloud firewalls).
  * **Brevo (Sendinblue) Integration**: When `BREVO_API_KEY` is present, emails are dispatched via Brevo's HTTPS REST API (`https://api.brevo.com/v3/smtp/email`) over port 443. Brevo provides un-sandboxed delivery to any recipient without requiring immediate custom domain verification.
* **Standard SMTP Fallback**: For environments with open outbound ports (local dev, bare-metal, or upgraded cloud tiers), standard `smtplib.SMTP` (port 587 STARTTLS) and `smtplib.SMTP_SSL` (port 465) remain fully supported.
* **Local In-Process Dev Capture**: Development environments retain an in-process mock SMTP capture server on `127.0.0.1:1025` with REST query endpoints on `127.0.0.1:8025`.
* **Container CA Certificates**: `ca-certificates` is explicitly installed in the runtime Docker container to ensure TLS handshake verification succeeds against external HTTPS APIs.

### 4. Operational In-Cloud Diagnostics & Error Transparency
* **Diagnostic Delivery Endpoint (`/health/test-email`)**: Exposes an endpoint that executes a live test delivery, probes outbound socket connectivity across ports 587, 465, 2525, 25, and 443, and reports provider activation status.
* **Exception Transparency**: In `mail.py`, delivery errors preserve underlying causes (`from exc`) and extract response bodies from `urllib.error.HTTPError`, eliminating silent failures and exposing upstream provider validation messages directly.

---

## Consequences and Compatibility

1. **Egress Independence**: Email verification and password resets function reliably across restricted cloud tiers without requiring paid platform upgrades or custom network tunnels.
2. **Backward Compatibility**: Existing local development workflows, unit tests, and integration test suites remain unchanged; in-process SMTP capture and local PostgreSQL fixtures continue to pass without network access.
3. **Operational Visibility**: Infrastructure drift, database disconnections, and provider authentication failures can be diagnosed immediately through structured health endpoints rather than container log inspection.
4. **Security Boundaries**: Strict CSRF protection, secure cookie handling, and CORS origins are preserved across cross-domain Vercel and Railway deployments.
