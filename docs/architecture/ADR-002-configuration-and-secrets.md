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

## Consequences

Local development needs no paid provider keys. Demo and production configuration remain distinct; free-tier provider limits do not satisfy production recovery or availability requirements.
