# Browser authentication contract

**Status:** Backend implementation delivered in Phase 2, 29 October 2026. Browser HTTPS acceptance remains a UI integration/release gate.
**Authority:** Updated LedgerPilot SRS and ADR-008; the user selected JWT access and stored refresh tokens.

FastAPI owns account registration, verification, passwords, MFA, sessions and recovery. PostgreSQL is the durable session source. The browser communicates with the API over the versioned API contract. Authentication endpoints and durable session/token tables are implemented in Phase 2; see `docs/phase-2-backend-foundation.md`.

The Phase 2–3 audit adds a database-maintained password generation (migration 0020). Sessions retain the generation of the password actually verified by login; protected requests and refresh reject a mismatch. This closes the race where an old-password login inserts its session after a concurrent reset has already revoked existing sessions. Existing access/refresh/MFA policies below remain unchanged.

- Access tokens are signed JWTs with a maximum 600-second lifetime. Validate the configured algorithm, signature, issuer, audience, expiry and session identity on the server. Token claims never establish business membership without server verification.
- Send browser authentication in HttpOnly, Secure cookies scoped to the API origin. No bearer token in localStorage or frontend environment variables. The implementation uses `__Host-lp_access`, `__Host-lp_refresh` and `__Host-lp_csrf`, all with path `/` and no Domain attribute. SameSite defaults to Lax; select None explicitly for a cross-site demo and verify actual browser behavior over HTTPS before release.
- Refresh tokens are opaque random secrets. Store a hash and session/token-family metadata server-side. Rotate on use in one transaction; detect replay and revoke the affected session family. Logout, account recovery and administrative revocation must invalidate durable sessions. Protected endpoints check durable session revocation, expiry and account suspension on every request. Refresh replay revokes the family in a committed transaction before the error response.
- Protect cookie-authenticated mutations with CSRF tokens and strict allowed-Origin checks. CORS alone does not implement CSRF protection. Frontend requests include credentials only through the canonical API client.
- Verify business membership and server-side permissions for each operation. Tenant headers, route/body identifiers and JWT claims do not independently authorize access.
- Use Argon2id for passwords. Implement privileged-role MFA, throttling, audit events and audited account recovery as required by the SRS. Never log passwords, JWTs, refresh/CSRF tokens, cookies or database URLs.
- `SESSION_SIGNING_KEY` is server-only. JWTs use pinned HS256, `JWT_KEY_ID`, issuer and audience validation. Optional `SESSION_PREVIOUS_SIGNING_KEY` and `SESSION_PREVIOUS_KEY_ID` support a controlled overlap; remove the old key after outstanding ten-minute access tokens expire. Retire a compromised key and revoke affected sessions; do not claim password rotation also revokes application sessions.

The SRS SEC-002 fixes seven-day absolute refresh expiry and fifteen-minute privileged idle expiry. These values are implemented. TOTP accepts one adjacent thirty-second step for transmission/clock skew and atomically consumes accepted counters. A dedicated `MFA_ENCRYPTION_KEY` protects factors; privately back it up and use an audited re-encryption procedure for rotation. Password resets and recovery-code use revoke prior sessions. Password reset retains the MFA requirement.

Cookies remain Secure even locally. HTTP liveness is not evidence of browser authentication. HTTPS ASGI tests verify API cookie attributes and session controls; a real-browser HTTPS/cookie/CORS check is required at UI integration/deployed acceptance.

## Phase 2 acceptance evidence

Test 10-minute expiry, invalid signature/issuer/audience, refresh rotation and replay, logout/recovery revocation, CSRF rejection, disallowed origins, privileged MFA and cross-business denials. Verify cookie attributes in a real browser over HTTPS and check that auth secrets never enter browser storage or logs.
