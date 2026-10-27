# Browser authentication contract

**Status:** Approved choices recorded for Phase 2 implementation, 27 October 2026.
**Authority:** Updated LedgerPilot SRS and ADR-008; the user selected JWT access and stored refresh tokens.

FastAPI owns account registration, verification, passwords, MFA, sessions and recovery. PostgreSQL is the durable session source. The browser communicates with the API over the versioned API contract. Authentication endpoints and session tables are Phase 2 deliverables.

- Access tokens are signed JWTs with a maximum 600-second lifetime. Validate the configured algorithm, signature, issuer, audience, expiry and session identity on the server. Token claims never establish business membership without server verification.
- Send browser authentication in HttpOnly, Secure cookies scoped to the API origin. No bearer token in localStorage or frontend environment variables. Select cookie names, paths and SameSite behavior with the actual web/API origin topology in Phase 2; verify them over HTTPS before release.
- Refresh tokens are opaque random secrets. Store a hash and session/token-family metadata server-side. Rotate on use in one transaction; detect replay and revoke the affected session family. Logout, account recovery and administrative revocation must invalidate durable sessions. Define access-token revocation checks before implementing protected endpoints.
- Protect cookie-authenticated mutations with CSRF tokens and strict allowed-Origin checks. CORS alone does not implement CSRF protection. Frontend requests include credentials only through the canonical API client.
- Verify business membership and server-side permissions for each operation. Tenant headers, route/body identifiers and JWT claims do not independently authorize access.
- Use Argon2id for passwords. Implement privileged-role MFA, throttling, audit events and audited account recovery as required by the SRS. Never log passwords, JWTs, refresh/CSRF tokens, cookies or database URLs.
- `SESSION_SIGNING_KEY` is server-only. Phase 2 defines the explicit JWT algorithm and key identifiers/rotation overlap. Retire a compromised key and revoke affected sessions; do not claim password rotation also revokes application sessions.

Refresh duration, idle timeout, signing-key rollover and precise browser cookie configuration must be settled against the Phase 2 SRS acceptance criteria and deployed origin topology. These values are not invented by Phase 1 service setup. HTTPS is required to verify the Secure-cookie workflow; local HTTP liveness is not authentication acceptance evidence.

## Phase 2 acceptance evidence

Test 10-minute expiry, invalid signature/issuer/audience, refresh rotation and replay, logout/recovery revocation, CSRF rejection, disallowed origins, privileged MFA and cross-business denials. Verify cookie attributes in a real browser over HTTPS and check that auth secrets never enter browser storage or logs.
