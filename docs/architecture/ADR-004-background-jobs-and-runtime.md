# ADR-004: Background jobs and runtime responsibilities

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 7, 8, 14

## Decision

- Keep HTTP authentication, authorization, validation, and synchronous financial workflows in the FastAPI application.
- Run long-running document extraction, notifications, exports, and approved forecasting as bounded asynchronous jobs where the SRS requires them.
- Keep the P1 runtime modular; an optional local worker may process development/demo jobs without changing business semantics.
- Production job delivery uses the SRS PostgreSQL outbox and AWS SQS path, with idempotent inbox/operation keys, bounded retries, backoff, dead-letter handling, and audited replay.
- Workers receive scoped service identity and tenant context from trusted job records. They never execute serialized code or arbitrary SQL and never post AI proposals without the approved human/policy gate.
- Do not introduce Redis Streams, a general-purpose task engine, leader-election scheduler, or WebSocket fleet merely because these exist in a reference ADR. Add one only through an SRS-backed decision.

## Consequences

The web request path stays responsive while financial effects remain durable and reviewable. P1 local/demo and commercial production may use different delivery infrastructure without changing ledger rules.
