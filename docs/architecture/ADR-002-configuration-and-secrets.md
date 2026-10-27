# ADR-002: Configuration and secrets

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 8, 12, 14, 15

## Decision

- Use Pydantic Settings v2 as the typed backend configuration boundary and fail fast on invalid production settings.
- Keep local values in ignored `.env`; commit names and safe local examples only in `.env.example`.
- Keep migration credentials separate from least-privilege runtime credentials. Use protected deployment secret storage for hosted environments.
- Redact credentials, tokens, connection strings, customer data, and document contents before logs or diagnostics.
- Keep all private service keys server-side. A Supabase service-role key is privileged and must never enter the frontend.
- Follow the SRS FastAPI-owned identity service. Supabase may be evaluated for PostgreSQL hosting or private object storage if its quotas, security, and recovery limits fit; Supabase Auth would require a superseding ADR and SRS/session-contract update.
- Record each production secret's purpose, environment, owner, rotation/revocation path, and failure behavior.

## Phase 1 implementation

Local PostgreSQL has three separate roles: a Compose bootstrap administrator, a runtime login and a migration login. Only the migration role owns the private `app` schema; runtime receives USAGE and default SELECT/INSERT/UPDATE plus sequence usage. Runtime has no default DELETE, DDL, replication or RLS-bypass privileges. Phase 2 migrations must narrow posted-ledger grants and grant any required identity cleanup or function execution explicitly. Default grants do not secure posted-ledger immutability by themselves.

The runtime Settings model requires a valid psycopg URL using `APP_DB_USER`, a random signing secret and explicit frontend origins. Hosted profiles require verified database TLS and HTTPS origins. Local setup is restricted to loopback on the configured `POSTGRES_PORT`; it never targets hosted PostgreSQL. Local pool configuration budgets five persistent plus five overflow connections per API process; the Phase 2 DB session factory implements the pool, pre-ping, transaction handling and statement timeouts. Recalculate the total across API/worker instances before scaling.

Use `python -m ledgerpilot.local_setup` for private local credential preparation and real role/grant checks. Migration/admin configuration is handled by this operator utility, not exposed as API Settings fields. See `docs/phase-1-service-setup.md` for credential ownership, rotation and local-only limits.

## Consequences

Local development needs no paid provider keys. Demo and production configuration remain distinct; free-tier provider limits do not satisfy production recovery or availability requirements.
