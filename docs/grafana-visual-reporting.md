# Grafana-Based Visual Financial & Audit Reporting

## 1. Overview & Architecture

LedgerPilot transforms accounting and audit reports from plain numeric tables into clear, verifiable visual reports while preserving 100% double-entry precision:

1. **Provisioned Grafana OSS + Image Renderer (`docker-compose.grafana.yml`, `deploy/grafana/`):**
   - `grafana/grafana-oss:11.3.0` paired with `grafana/grafana-image-renderer:latest`.
   - Datasource (`uid: ledgerpilot-postgres-ro`, `editable: false`) connects to PostgreSQL 16 using the dedicated **`ledgerpilot_grafana_ro`** read-only role (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS`, `default_transaction_read_only = on`, `statement_timeout = '15s'`).
   - Migration `0043_grafana_readonly_reporting.py` grants `FOR SELECT` RLS policies (`session_user::text = 'ledgerpilot_grafana_ro'`) **only** on the 7 reporting tables (`businesses`, `accounts`, `journals`, `journal_lines`, `trade_documents`, `payments`, `financial_audit_runs`) — zero access to auth, MFA, encryption keys, or supplier portal credentials — and installs 7 `STABLE` SQL aggregation functions (`app.grafana_*`).
2. **Version-Controlled Variable Dashboards (`allowUiUpdates: false`):**
   - **`deploy/grafana/dashboards/ledgerpilot-financial-reports.json` (`uid: lp-financial-reports`):**
     - Variables: `$business_id`, `$period_mode` (`monthly` | `yearly` | `custom`), `$account_kind` (`expense` | `income`), `$currency`.
     - Panels `1`–`4`: KPI stat cards (`Total Income`, `Total Expenses`, `Net Balance / Profit`, `Cash Balance` with red `< 0` deficit threshold).
     - Panels `5`–`10`: Income vs. Expense Trend (line/area), Category Breakdown (top 6 + `"Other categories"` horizontal bars), Monthly/Yearly Comparison (grouped bars), Cash Flow & Running Balance Trend, Balance Sheet Structure (`Assets` vs. `Liabilities` vs. `Equity`), and Top Accounts/Entries by Amount.
   - **`deploy/grafana/dashboards/ledgerpilot-audit-reports.json` (`uid: lp-audit-reports`):**
     - Variables: `$business_id`, `$audit_type` (`all` | `monthly` | `yearly` | `emergency` | `auto_balance_sheet` | `auto_period_close`).
     - Panels `1`–`4`: KPI stat cards (`Total Issues`, `Critical Issues`, `High Priority`, `Runs Evaluated`).
     - Panels `5`–`7`: Audit Findings by Severity, Findings by Category, and Findings Over Time.
3. **Explicit Dual-Path Rendering & Graceful Degradation (`backend/src/ledgerpilot/reports/visuals.py`):**
   - **Web UI (`/reports`, `/`, `/financial-audit`):** Displays accessible SQL-verified charts alongside live Grafana `/d-solo/...` iframe embeds (when `GRAFANA_EMBED_ENABLED=true` and `GRAFANA_URL` is reachable), wrapped in `ChartErrorBoundary` so any chart error degrades cleanly to the supporting accounting table.
   - **PDF, HTML & Email Exports (`/reports/visual-export`, `/reports/email-visual`, `/audits/{id}/export`, `/audits/{id}/email`, and queued `POST /reports/export`):** Calls Grafana Image Renderer (`/render/d-solo/...`) with a bounded LRU+TTL cache (`GRAFANA_RENDER_CACHE_TTL_SECONDS`). When Grafana is offline or unconfigured (e.g. serverless cloud environments), LedgerPilot **explicitly discloses** the fallback and renders matching PNG charts server-side via `matplotlib` + `reportlab` from the exact same SQL queries. If chart rendering fails altogether, exports still succeed in `table_only_degraded` mode.

---

## 2. How to Run Grafana Locally

1. Apply database migrations so `0043_grafana_readonly_reporting` creates the RLS policies and `app.grafana_*` SQL functions:
   ```powershell
   Set-Location backend
   uv run alembic upgrade head
   Set-Location ..
   ```
2. Add the Grafana credentials to your root `.env` (never commit real secrets; `GRAFANA_DB_PASSWORD` must be at least 16 characters):
   ```dotenv
   GRAFANA_DB_PASSWORD=<generate-strong-24-char-readonly-db-password>
   GRAFANA_ADMIN_USER=admin
   GRAFANA_ADMIN_PASSWORD=<generate-strong-admin-password>
   GRAFANA_PORT=3001
   GRAFANA_URL=http://127.0.0.1:3001
   GRAFANA_PUBLIC_URL=http://127.0.0.1:3001
   GRAFANA_DEFAULT_THEME=light
   GRAFANA_EMBED_ENABLED=false
   ```
3. Start PostgreSQL, the read-only role bootstrapper, Grafana Image Renderer, and Grafana OSS:
   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.grafana.yml up -d --wait
   ```
4. *(Optional — Service Account Token for authenticated `/render/d-solo` PNG exports)*:
   - Open `http://127.0.0.1:3001`, sign in as `GRAFANA_ADMIN_USER`, navigate to **Administration → Users and access → Service accounts**, create a **Viewer** service account token, and set `GRAFANA_API_TOKEN=glsa_...` in `.env`.
   - To allow browser `<iframe>` embeds on the same approved host/CSP origin, set `GRAFANA_EMBED_ENABLED=true`.

---

## 3. How to Add a New Chart

1. **SQL Aggregation Function (Read-Only):**
   - Create a new `STABLE` SQL function in an Alembic migration under `app.grafana_<chart_name>(p_business_id uuid, ...)` and `GRANT EXECUTE ... TO PUBLIC` (RLS on underlying `app.*` tables remains enforced).
   - Never grant `INSERT`, `UPDATE`, `DELETE`, or `TRUNCATE` to `ledgerpilot_grafana_ro`.
2. **Grafana Dashboard JSON (`deploy/grafana/dashboards/*.json`):**
   - Add a new panel object with the next `id` (e.g. `11`), `"datasource": { "type": "postgres", "uid": "ledgerpilot-postgres-ro" }`, and `"rawSql"` calling `SELECT * FROM app.grafana_<chart_name>('$business_id'::uuid, $__timeFrom()::date, $__timeTo()::date)`.
   - Keep `min: 0` on bar axes so magnitudes are never visually truncated, and stick to the cohesive palette (`#274b7a` navy, `#1f7a5c` emerald, `#b86b2b` copper, `#4c638a` slate, `#b3353e` crimson).
3. **Backend Renderer & Fallback Map (`backend/src/ledgerpilot/reports/visuals.py`):**
   - Register the new panel ID in `_PANEL_MAP_FINANCIAL` or `_PANEL_MAP_AUDIT` and add the matching `chart_kind` branch in `render_matplotlib_chart_png` so PDF/HTML/email exports remain identical even when Grafana is unreachable.
4. **Web UI Component (`frontend/src/features/reports.tsx` or `financial-audit.tsx`):**
   - Place the chart inside `<ChartErrorBoundary>` right above or beside its supporting `<Table>` so every plotted figure remains verifiable against posted ledger lines.

---

## 4. How to Change the Theme (Light / Dark)

- **Global Default:** Set `GRAFANA_DEFAULT_THEME=light` or `GRAFANA_DEFAULT_THEME=dark` in `.env`. Both Grafana `/d-solo` embeds, `/render/d-solo` PNG renders, and server-side `matplotlib` fallbacks honor this setting (`#fefdfb` warm paper light background vs. `#1b2638` slate dark background).
- **Per-Export Override:** Pass `theme=dark` or `theme=light` on:
  - `GET /api/v1/businesses/{business_id}/reports/visual-export?report_type=monthly-summary&format=pdf&theme=dark`
  - `POST /api/v1/businesses/{business_id}/reports/email-visual` (`{ "theme": "dark", ... }`)
  - `GET /api/v1/businesses/{business_id}/audits/{audit_id}/export?format=pdf&theme=dark`
- **Web Client:** Toggle the dark/light theme button in the top application bar — CSS custom properties in `frontend/src/styles.css` (`[data-theme="dark"]`) automatically adapt `.visual-card`, `.visual-kpi-card`, axis labels, and grid lines.

---

## 5. Assumptions & Limitations

1. **RLS & Multi-Tenancy Boundary:** PostgreSQL `FORCE ROW LEVEL SECURITY` is active across `app.*`. Rather than granting `BYPASSRLS` (which would expose `users`, `sessions`, and encrypted keys), migration `0043` scopes `grafana_readonly_select` strictly to the 7 financial/audit reporting tables. Dashboards filter by `$business_id`; therefore direct Grafana UI access must remain restricted to operators, while end users consume tenant-authorized API exports and scoped embeds.
2. **Browser CSP & Cross-Origin Iframes:** `frontend/security-policy.ts` enforces a strict Content Security Policy without wildcard `frame-src`. Live `<iframe>` embedding (`GRAFANA_EMBED_ENABLED=true`) requires serving Grafana behind the same origin (e.g. reverse-proxied path) or an explicitly configured proxy; otherwise the web client automatically renders built-in accessible SVG/HTML charts from the same verified SQL endpoint.
3. **Cash vs. Accrual SQL Functions:** Canonical web/PDF/HTML report endpoints (`query_financial_visuals`) compute both `cash` and `accrual` basis figures through `ledgerpilot.reports.service.financial`, guaranteeing exact cent-for-cent parity with the Profit & Loss and Balance Sheet tables. Direct Grafana SQL panels query posted ledger lines (`accrual` journal dates) for real-time time-series exploration.
4. **Hosted Serverless Deployments:** Hosted platforms that do not run the optional `docker-compose.grafana.yml` sidecar stack (such as single-container Railway or Vercel previews) automatically use the disclosed server-side `matplotlib` + `reportlab` fallback so PDF, HTML, and emailed reports always include crisp embedded charts.
