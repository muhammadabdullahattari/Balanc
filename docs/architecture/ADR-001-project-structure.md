# ADR-001: Repository structure and package management

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 2, 8, 14; implementation plan phases 0–2

## Context

LedgerPilot needs one reviewable repository for its Python API, responsive web client, tests, deployment assets, and product documentation. The accounting system must remain a modular monolith in P1.

## Decision

- Keep `backend/`, `frontend/`, `docs/`, `deploy/`, and `.github/` at the repository root.
- Use a Python `src/ledgerpilot/` package and a backend-owned `pyproject.toml` plus committed `uv.lock`.
- Import application modules as `ledgerpilot.*`, never `src.ledgerpilot.*`.
- Keep React/TypeScript/Vite and its npm manifest/lockfile in `frontend/`.
- Keep Alembic migrations in `backend/migrations/`; synthetic fixtures and tests remain within the backend/frontend test boundaries.
- Do not add worker, scheduler, CLI, SDK, or native-app packages unless a LedgerPilot SRS release phase requires them.

## Consequences

Fresh setup can be reproduced with `uv sync --locked` and `npm ci`. The modular package boundaries allow feature development without premature service splitting.
