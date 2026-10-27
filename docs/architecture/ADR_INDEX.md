# LedgerPilot Architecture Decision Records

This index records LedgerPilot decisions derived from the approved SRS and implementation plan. The files supplied from `D:/One/task-engine` are reference material from another product; only compatible engineering practices were adapted. Task-engine-specific decisions are not LedgerPilot decisions.

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](ADR-001-project-structure.md) | Repository layout and `uv` package management | Accepted |
| [ADR-002](ADR-002-configuration-and-secrets.md) | Typed settings and secret handling | Accepted |
| [ADR-003](ADR-003-data-model-and-persistence.md) | PostgreSQL, SQLAlchemy, psycopg, and Alembic | Accepted |
| [ADR-004](ADR-004-background-jobs-and-runtime.md) | API and optional worker responsibilities | Accepted |
| [ADR-005](ADR-005-api-surface-and-schemas.md) | Versioned REST API and OpenAPI contract | Accepted |
| [ADR-006](ADR-006-durable-job-delivery.md) | PostgreSQL outbox and production SQS delivery | Accepted |
| [ADR-007](ADR-007-observability.md) | Logs, metrics, traces, and health probes | Accepted |
| [ADR-008](ADR-008-security-and-multitenancy.md) | Session security, roles, and business isolation | Accepted |
| [ADR-009](ADR-009-test-strategy.md) | Phase-gated test strategy | Accepted |

## Changing a decision

An architecture change requires a new or superseding ADR, impact review against the SRS, API/data compatibility review, and updated acceptance checks before implementation. Do not mark an ADR Accepted merely because a reference document from another product did so.
