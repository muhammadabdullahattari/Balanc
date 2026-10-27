# Reference document reconciliation

The supplied `D:/One/task-engine` standards and ADRs describe a separate event-driven task-processing product. They are reference material, not LedgerPilot requirements. The LedgerPilot SRS and current implementation plan control product scope and architecture.

## Practices adapted

- Focused changes, caller/contract review, regression checks, no remote push without permission, and one canonical implementation per capability.
- Pydantic Settings, validated production configuration, secret redaction, SQLAlchemy/Alembic persistence, OpenAPI contracts, structured observability, tenant-aware authorization, and layered tests.
- A single typed frontend API client, server-state caching, accessible UI, secure sessions, and real-service integration checks.

## Product-specific decisions excluded or replaced

- The LedgerPilot SRS selects React/Vite. Next.js App Router, `next/*`, and Next-specific route conventions do not apply.
- The SRS selects PostgreSQL, SQLAlchemy, Alembic, and psycopg; the reference `asyncpg` driver is not adopted. Keep the dependency manifest and plan aligned with the SRS choice.
- The SRS uses a PostgreSQL outbox and AWS SQS for production jobs. Redis Streams, `XREADGROUP`, task broker adapters, task leases, task scheduler APIs, and task-engine DLQ endpoints are not LedgerPilot architecture.
- LedgerPilot uses its SRS-defined auth lifecycle and roles. Do not trust `X-Tenant-Id` or import the task engine's admin/developer/viewer/worker roles.
- Task-engine task, queue, broker, worker, scheduler, and throughput endpoints and its task-specific test IDs are excluded.
- General no-fake-production-code guidance is retained. Contract-accurate test doubles are allowed only in isolated tests and the implementation plan's pre-integration UI stage; they are never production behavior.

When a supplied reference document and the LedgerPilot SRS disagree, record the discrepancy and follow the SRS unless the user approves an SRS change.
