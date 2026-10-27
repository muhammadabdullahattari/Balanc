# ADR-009: Test strategy and API validation

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 15–18; implementation plan phases 0–12

## Decision

- Map each testable work item to SRS requirement IDs, expected accounting effects, authorization boundary, and captured evidence.
- Use unit and property-based checks for money, allocation, period, idempotency, and domain rules. Use real PostgreSQL for migrations, constraints, transactions, isolation, concurrency, and reconciliation tests.
- Complete P1 backend models, schemas, services, routers, report endpoints, and frontend-required APIs before frontend feature work.
- Gate frontend development on backend pytest/API coverage and reviewed OpenAPI/Swagger workflows. Swagger UI complements automated checks; it does not replace them.
- Run UI-only component/browser checks at the frontend gate. Test doubles are limited to those isolated checks and are removed from production builds.
- Run API integration checks against the real backend and database before deployment. Run deployed end-to-end/system checks against the release candidate before public API/SDK work.
- Test accounting golden cases including cash/accrual timing, invoice/payment allocation, loans, transfers, unknown deposits, tax classification, reconciliation, and cross-tenant denial.
- Keep the public developer API and SDK behind the deployed E2E and security acceptance gate specified by the plan.

## Consequences

Evidence is phase-specific and repeatable. A green UI build or successful Swagger click-through alone cannot establish accounting correctness or release readiness.
