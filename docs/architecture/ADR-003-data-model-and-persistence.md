# ADR-003: Data model and persistence

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 3, 6, 8, 10, 14

## Decision

- PostgreSQL is the authoritative store for accounting and tenant-owned application records.
- Use SQLAlchemy 2.x with the SRS-selected psycopg v3 driver and Alembic migrations. Use exact numeric types for money, UUID identifiers, explicit business timezone, and UTC event timestamps.
- Apply tenant-aware composite relationships and database constraints/row policies as defense in depth. Application authorization remains mandatory.
- Posted journal rows are append-only. Enforce balanced, atomic, idempotent posting; closed-period guards; deterministic rounding; reversals; and subledger/control-account reconciliation.
- Keep file originals and immutable evidence manifests in private object storage when the release profile supports it; PostgreSQL stores scoped metadata and integrity references.
- Use an outbox for durable work publication when a workflow crosses the database and asynchronous processing boundary. A broker never becomes the accounting source of truth.
- Introduce caching, partitioning, replicas, or sharding only after measured workload and correctness evidence justify them.

## Consequences

Financial writes remain transactional and auditable. Read replicas and caches may serve only explicitly stale-tolerant data and must disclose freshness where required.
