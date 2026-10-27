# ADR-005: API surface and schemas

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 8, 11, 14, 15

## Decision

- Expose a versioned FastAPI REST API for the first-party web client. FastAPI/Pydantic schemas define request and response contracts; SQLAlchemy models are persistence details, not API objects.
- Publish OpenAPI 3.1 and verify routes, errors, permissions, pagination, idempotency, and financial side effects before the web feature screens are built.
- Keep internal first-party REST APIs distinct from the later public developer API and SDK. The public surface is gated by the SRS roadmap and deployed end-to-end acceptance.
- Use a consistent machine-readable error envelope with request/correlation identifiers and user-safe messages.
- Derive tenant access from the verified principal and business membership. Client-provided tenant IDs and roles are selectors to validate, never proof of authorization.
- Add SSE, webhooks, or other streaming interfaces only where a specific LedgerPilot requirement calls for them. Do not inherit task-engine WebSocket endpoints.

## Consequences

The frontend can generate a typed client from a stable contract, while the backend remains authoritative for permissions, business policy, and financial calculations.
