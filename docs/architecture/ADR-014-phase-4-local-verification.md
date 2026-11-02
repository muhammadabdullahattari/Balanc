# ADR-014: Phase 4 verification scope and browser contract

Date: 30 October 2026. Status: accepted for the local pilot, following the user's explicit choice: **"Verify approved local pilot; record SRS gaps."**

## Decision

Verify the approved local implementation in ADR-010–013 without representing it as complete compliance with every detailed P1 SRS clause. Keep the SRS unchanged and maintain an explicit difference register and requirement-to-test pointers. Do not silently remove requirements, lower production targets or treat a test pointer as a passing assertion for an entire requirement.

Preserve FastAPI's modular monolith, PostgreSQL authority, forced RLS, immutable posting, existing providers and the implementation phase sequence. Generate the internal browser API types from the reviewed OpenAPI with `openapi-typescript`; use `openapi-fetch` through one browser transport for cookie credentials and CSRF. This is frontend infrastructure, not the Phase 11 public developer API/SDK. Abort signals and typed errors follow the library contract; writes have no automatic retry or automatic refresh/replay. Session recovery and TanStack Query integration remain Phase 7 work.

Local/test Swagger uses the same cookie/CSRF/Origin controls as the application, with a same-origin request interceptor fetching the CSRF token. It does not add the docs origin to configuration automatically. The operator must explicitly include the local docs origin. Demo/staging/production docs and schema endpoints remain disabled. FastAPI's existing external Swagger assets are used only for local/test documentation.

## Consequences

Local pilot verification may close after its recorded checks pass while identified SRS differences remain open. This is not a production security, capacity, recovery, complete P1 feature or SRS coverage certification. SRS branch coverage thresholds remain unmet until measured otherwise; CI publishes coverage rather than pretending the target passed. The full SRS/release gate must resolve those differences before claiming compliance. The detailed [verification record](../phase-4-backend-verification.md) owns results and follow-up triggers.
