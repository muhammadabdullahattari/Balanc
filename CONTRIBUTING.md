# Contributing to LedgerPilot

LedgerPilot is a web-first finance application. Keep changes inside the approved SRS release scope and link work to an SRS requirement or an approved architecture decision. Do not add native mobile applications or expose database/provider secrets to the browser.

## Local development

1. Install Git, Python 3.12, uv, Node.js 22, npm and Docker Desktop with Compose.
2. Follow the root README to prepare `.env`, start PostgreSQL, and run the API and web client.
3. Backend: `cd backend; uv sync --locked --all-groups`.
4. Frontend: `cd frontend; npm ci`.
5. Run the backend gates from `backend/`: `uv run ruff check src tests`, `uv run mypy`, `uv run coverage run -m pytest -q`, `uv run coverage report`, and `uv run python -m compileall -q src`.
6. Run the web production gate from `frontend/`: `npm run build`.
7. Build the API image from the repository root: `docker build -f backend/Dockerfile -t ledgerpilot-api:local backend`.

Tests should use synthetic data and must not require paid provider credentials. Database-backed integration tests must use the local/test PostgreSQL service, never production data.

## Branches and pull requests

Use short-lived feature branches from the selected repository default branch. Keep each PR focused and link the SRS ID, acceptance criteria, migrations, security/tenant impact and evidence. The GitHub issue templates capture this information for feature and defect work. Require backend, frontend and container CI checks before merging to the selected default branch. Do not push or publish releases from an agent-run task unless the user explicitly asks.

Before requesting review, run the relevant local gates and include actual results. A skipped or environment-blocked check is not a pass. Financial changes must explain journal effects, basis/date handling, idempotency and reversals where relevant. Include tests for authorization and tenant isolation in addition to the happy path.

## Database changes

Use Alembic migrations for schema changes. Keep migrations additive where possible, test upgrade and restore behavior, and do not edit shared or deployed databases manually. Synthetic domain fixtures belong with the schema and tests that define their accounting meaning; the Phase 0 database reset procedure is for disposable local data only.

