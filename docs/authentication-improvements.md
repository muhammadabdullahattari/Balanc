# Authentication improvements and security review

Date: 2 November 2026. Scope: user-requested identity improvements after Phase 8.

## Implemented

- Shared password visibility control for sign-in, registration and reset. The eye is a non-submitting, labeled toggle; values stay in transient form state. Switching auth modes clears entered secrets.
- New registration/reset passwords require 15-128 characters, an ASCII uppercase letter, lowercase letter, digit and a non-whitespace special character. NFC normalization, the compromised-password list and Argon2id hashing remain. Existing password hashes can still sign in; new/reset credentials follow the requested stronger policy.
- Friendly login, MFA, expired/replaced-code, CSRF, delivery and rate-limit messages. Validation types become actionable text; technical correlation IDs are tucked under Support reference. Rejected login credentials do not erase the form or invalidate another valid session.
- Resend verification with an editable address, real 60-second UI cooldown and server Retry-After handling. Only the newest successfully issued verification/reset code remains usable. Codes expire after 15 minutes; invitations retain their separate 48-hour validity.
- Three successful verification issuances without verification pause new requests for 24 hours. Re-registration and resend share the same durable counter and row lock. The third code remains usable for its normal lifetime. Successful verification clears the counter. Requests to verified accounts remain generic and do not change their password or lock them out. Existing distributed IP/email rate limits remain active; this is a defined abuse rule, not an unspecified machine-learning threat detector.
- Pending email/password registration is bound to the requesting browser's CSRF hash, preventing another signup browser from activating a different pending password. Re-registering an unverified account replaces its pending password and invalidates earlier codes atomically. Resend only issues in the bound browser; other browsers get the same generic response without email or account disclosure. Restart sign-up with your chosen password if you changed browser/address or cleared cookies. Legacy unverified accounts should restart sign-up; verified accounts are preserved.
- MFA enrollment has a locally rendered QR code and the existing manual setup key. No QR generation service receives the secret. Bad/non-ASCII TOTP input fails safely; incorrect MFA codes can be retried without clearing the authenticated enrollment session.
- Optional Google Identity Services button and backend ID-token verification through Google's official library. Signed issuer/audience/expiry/verified-email/sub checks, a five-minute one-use nonce bound to the requester's CSRF hash, bounded certificate requests and concurrency limits protect the exchange. Google proofs are not stored. LedgerPilot still issues/revokes its own cookies and requires its own MFA.
- Existing email accounts are never silently linked by email. Sign in and complete MFA, then use Settings > security > Link your Google account. The Google email must match the local account. Google Gmail/Workspace addresses can bootstrap a verified account. Other Google account email addresses must create/verify a local account first, then explicitly link it; this avoids trusting stale third-party email ownership.

## Where your development emails are

The selected service is **Mailpit**, not outbound personal email delivery. Open http://127.0.0.1:8025. Find the message addressed to your entered address and paste its newest code into the same app browser/address where you signed up. The UI exposes this mailbox link only in the local loopback SMTP profile. Nothing is expected to arrive in Gmail or another real mailbox with this configuration. Real SMTP remains a later environment choice; no provider was provisioned and no paid service was enabled.

## Enable Google when ready

Google sign-in is implemented but disabled until GOOGLE_CLIENT_ID is set. No live Google login is claimed without your own configured client.

1. In Google Auth Platform, create/select a project, configure branding/audience, and create an OAuth client of type Web application. Add test users if the consent configuration requires them.
2. For local testing, register http://localhost and http://localhost:5173 as Authorized JavaScript origins. Use that exact app address. Register the exact HTTPS web origin for deployment. Google controls allowed origin formats; use its documented localhost setup for local testing.
3. In the ignored root .env, add GOOGLE_CLIENT_ID= followed by your real value ending in .apps.googleusercontent.com. A commented example is in .env.example. Do not set a client secret in the frontend. This GIS JavaScript-callback flow requires the client ID and server signature validation, not a browser-held client secret or an authorization-code callback URL.
4. Restart the API and reload the frontend. The API supplies this public client ID and enables the official Google-rendered button; the frontend has no duplicate provider key. Keep the backend FRONTEND_ORIGINS exact. The local configuration now allows localhost:5173 and 127.0.0.1:5173; use one address consistently for verification cookies.
5. Google never replaces LedgerPilot's tenant/role/session authority. Existing users link from the security tab after MFA. Enter your LedgerPilot authenticator/recovery code before choosing Google at sign-in when MFA is already enrolled.

Google's SDK is loaded only when configured. A network/load failure offers email/password fallback. Local Vite and deployed Vercel header settings account for Google popup/referrer requirements. Actual consent, Google project policy and deployed browser-cookie behavior require verification after configuration; isolated tests do not certify those external settings.

Official references: [Google setup](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid), [server-side ID-token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).

## Are the tokens secure, and why are they visible?

Your own browser's Network/Application tools can inspect cookies and responses. Secure/HttpOnly flags restrict delivery and JavaScript access; they do not hide cookies from the browser owner. Authentication still requires protecting the device, avoiding XSS and using HTTPS on hosted deployments.

- Access token: signed HS256 JWT with issuer/audience/key-ID/expiry validation and ten-minute lifetime. It carries identity/session IDs, not password or financial records. It is held in a Secure, HttpOnly, host-only cookie. Its signature protects integrity; JWT contents are not encrypted.
- Refresh token: cryptographically random opaque token in a Secure/HttpOnly host-only cookie. PostgreSQL stores its SHA-256 digest, not the raw token. Rotation consumes the old token; replay revokes the family. Logout/reset/recovery and password generations enforce revocation; absolute and privileged idle timeouts remain.
- CSRF: the browser needs the raw random value in the protected same-origin response to send X-CSRF-Token. The API checks the allowed Origin, cookie/header equality and authenticated session hash. It is not an authentication token. Hashing it in the response would not add protection to this protocol. Session/verification/Google-challenge bindings store digests server-side.
- Passwords use Argon2id; random verification/recovery/refresh tokens use SHA-256 digests because they already have high entropy. MFA secrets are encrypted at rest with the separate MFA encryption key. QR/setup secrets and recovery codes are intentionally shown to their enrolling user with no-store responses.
- Sensitive auth responses are no-store. Credentials/provider proofs do not enter localStorage, durable client caches, audit attributes or request logs. Only a theme preference uses localStorage. JSON data is protected in transit with HTTPS in hosted environments; responses are not indiscriminately hashed/encrypted into something the UI cannot read.

This is a source-and-test-backed local security review, not a guarantee against every attack. The existing hosted TLS/cookie/CORS gates, production secrets/retention/nonce cleanup procedures, and three moderate development-tool findings remain tracked. No new hosted deployment, external email delivery or live Google consent was performed.

## Schema and verification

Additive migrations 0021 and 0022 add verification issuance state, Google subject/challenge tables with forced RLS and verification-browser hash binding. Local development has been upgraded without deleting users/books. Runtime and readiness now require 0022. Older migration files were preserved; the second safeguard is a new migration because 0021 had already been applied.

New tests use real PostgreSQL/Mailpit. Google certificate transport is substituted only in isolated tests with locally generated RSA certificates; the real official verification library verifies valid and invalid signed JWTs. This checks signature/audience/issuer/expiry, CSRF/nonce/replay, explicit linking, MFA, non-authoritative-email handling and owned-cookie behavior without pretending a live Google project exists. Daily issuance concurrency, expiry and cross-browser verification rejection have dedicated checks. Frontend tests cover eye toggles, policy, resend/cooldown/Mailpit, friendly errors, local QR and Google API wiring. Final evidence is recorded in docs/contracts/identity-hardening-results.json after checks complete.

## Completion update - 3 November 2026

Local verification is complete: 58 focused backend security/identity checks, 33 UI checks, eight transport checks, ten isolated Chromium regressions and two real Chromium/API/PostgreSQL journeys passed. The real signup journey includes password visibility, resend/old-code rejection, successful login and QR enrollment with an actual generated TOTP. Earlier intermediate environment/test failures were repaired or retested and are not counted as passing. Details are in [the prioritized security review](password-session-csrf-security.md) and [results](contracts/password-session-csrf-results.json).

The root Google client ID is now configured and format-valid. The public response allowlist is verified by tests; the configured client ID was format-validated without printing its value. Live Google account consent/Cloud origin acceptance remains unverified; do not interpret the isolated provider-signature fixtures as a real Google login. Mailpit continues to capture development emails locally. Migrations 0021/0022 were applied to the local development database on 2 November without deleting user or ledger data.

The subsequent focused review adds hosted HTTP rejection/HSTS, a loopback-only HTTP exception in the browser transport, sign-out-everywhere cookie clearing and short test tracebacks. Production TLS/proxy acceptance remains Phase 9/10. Nothing was committed, pushed or deployed.
