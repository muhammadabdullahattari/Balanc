# ADR-007: Observability

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 7, 12, 13, 15

## Decision

- Emit structured application logs with request/correlation IDs and appropriate tenant-safe operational context.
- Measure API latency/errors, database pool and query behavior, job backlog/age/failures, import/reconciliation outcomes, and AI proposal quality/cost where enabled.
- Use OpenTelemetry-compatible traces and metrics; production exports to the SRS AWS observability path. Local development may use free local tooling.
- Provide liveness and readiness probes. Readiness checks only required dependencies and does not expose credentials or customer data.
- Redact secrets and minimize financial/personal payloads before logs, traces, metrics labels, or error reports. Never use raw tenant/user identifiers as unbounded metric labels.
- Publish SLOs only after workload measurement and staffed operational support. A free demo has explicit quotas and no production SLA.

## Consequences

Operators can diagnose service and accounting workflow failures without leaking sensitive data or presenting unmeasured targets as guarantees.
