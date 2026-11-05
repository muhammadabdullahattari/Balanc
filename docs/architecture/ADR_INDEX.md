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
| [ADR-010](ADR-010-phase-2-auth-and-posting.md) | Phase 2 authentication, tenant context and posting implementation | Accepted |
| [ADR-011](ADR-011-phase-3-sources-and-local-extraction.md) | Business sources and isolated local extraction profile | Accepted for local P1 |
| [ADR-012](ADR-012-reviewed-opening-balances.md) | Accountant-reviewed cutover balances and equity offsets | Accepted for local P1 |
| [ADR-013](ADR-013-local-p1-closure.md) | Reviewed suggestions, resumable exports and P1 completion controls | Accepted for local P1 |
| [ADR-014](ADR-014-phase-4-local-verification.md) | Local verification scope, generated browser contract and secure Swagger transport | Accepted for local pilot |

| [ADR-015](ADR-015-google-and-verification-hardening.md) | Optional Google federation, browser-bound verification and requested password/abuse rules | Accepted by user scope |

| [ADR-016](ADR-016-report-snapshots-and-pilot-deployment.md) | Consistent report snapshots and durable readiness-gated pilot deployment | Accepted within audit remediation |

| [ADR-017](ADR-017-floci-pilot-target.md) | Floci replaces AWS for Phase 9 pilot hosting | Target accepted; compatibility verification pending |

## Changing a decision

An architecture change requires a new or superseding ADR, impact review against the SRS, API/data compatibility review, and updated acceptance checks before implementation. Do not mark an ADR Accepted merely because a reference document from another product did so.
