# Password, session cookie and CSRF security review

Date: 3 November 2026. Scope: the user's three prioritized controls.

## Password protection

Passwords are transmitted to the backend for Argon2id verification and never stored in plaintext. New/reset passwords follow the requested composition/minimum length policy; existing hashes remain valid for login. Request validation responses contain field locations/types, not rejected values. Application logging records request metadata/error classes instead of request bodies, cookies, passwords or signing/database credentials. Pytest now uses short tracebacks to avoid expanded connection-argument dumps on test failures.

The browser API transport now rejects remote HTTP even when the API and page share that origin. Same-origin loopback HTTP remains available for local development. Hosted API profiles reject non-HTTPS requests before reading credential bodies, and return HSTS on HTTPS responses. GET/HEAD health probes remain available to private infrastructure checks. A rejected HTTP request cannot undo a client's prior plaintext transmission: production must expose HTTPS at the edge and keep port 8000 private.

The backend uses the ASGI scheme supplied by the server, never an untrusted X-Forwarded-Proto header directly. Configure Uvicorn FORWARDED_ALLOW_IPS for the actual trusted TLS reverse-proxy peers; never use a publicly reachable wildcard. Real TLS/certificate/proxy acceptance remains a deployment gate.

## Access and refresh protection

Access and refresh cookies use __Host- names, Path=/, no Domain attribute, Secure, HttpOnly and configured SameSite (default Lax). The access JWT is signed and validated for algorithm, key ID, issuer, audience and ten-minute lifetime; it contains no password. It is signed, not encrypted. Every protected request also verifies current session/user revocation and password generation.

Refresh tokens are high-entropy random values. Only SHA-256 digests are persisted. Rotation consumes the old token under a family lock; reuse revokes the family. Server-side absolute expiry (seven days), privileged inactivity checks, password-reset/recovery revocation and logout remain authoritative even if a browser retains a stale cookie. Both normal logout and sign-out-everywhere now clear all three browser cookies with the same security attributes as issuance.

No credentials are persisted in localStorage/sessionStorage. The only localStorage value is a theme preference. The public Google client ID needs no secrecy; no Google client secret is used by this GIS flow.

## CSRF validation

Unsafe methods require an exact allowed Origin, a nonempty ASCII cookie/header token and constant-time cookie/header equality. Authenticated writes additionally compare the token digest with the stored session binding. Forging a matching cookie/header pair therefore cannot bypass the session check. Refresh validates CSRF before applying replay revocation. Signup verification and Google nonce proofs retain their additional browser/session binding.

The raw CSRF value in the no-store response is intentional: the legitimate frontend needs it for X-CSRF-Token. It is not an authentication credential and must not be replaced with a cosmetic response hash. Google challenge and session bindings store hashes server-side.

## What browser inspection can show

The browser owner can inspect their own password request, cookie values, CSRF response, Google ID proof and MFA setup/recovery response. HttpOnly prevents JavaScript cookie reads; it does not hide values from DevTools. HTTPS protects network transit; it does not hide decrypted traffic from the browser itself. The controls keep server secrets out of public responses/bundles/logs, avoid persistent script-readable credentials and enforce authorization/rotation/CSRF server-side. DevTools-blocking tricks or client-side encryption would not provide that boundary.

## Verification

58 focused backend checks passed against isolated real PostgreSQL/Mailpit, covering password hashes and no-leak responses/logs, cookie flags, rotation/replay/expiry, sign-out-everywhere clearing, Origin/cookie/header/session CSRF checks, hosted HTTP rejection before body consumption and forged-forwarding rejection, plus the identity/Google/MFA input regressions. 33 component/accessibility checks and eight browser transport checks passed. Configured mypy (91 files), Ruff, generated OpenAPI (126 paths), inventory (122 call sites/94 operations), production build and formatting passed.

The built 24-file bundle was checked against configured private values without printing them: zero matches. Root .env is untracked and production source maps are absent. This is a bounded check of current configured values, not a universal secret scanner or penetration test. Two real Chromium/API/PostgreSQL journeys passed, including cookie/session financial effects and the complete signup/resend/replaced-code rejection/login/QR enrollment flow. Ten isolated UI browser regressions also passed. Final results: [sanitized evidence](contracts/password-session-csrf-results.json). Fixture teardown removed synthetic credentials. Previous local-service startup failures are not represented as passing evidence.

No hosted TLS deployment or live Google consent was performed. Existing three moderate development-generator findings and Phase 10 manual browser acceptance remain tracked. No secrets were intentionally printed, no data volumes were reset, and nothing was committed/pushed/deployed.
