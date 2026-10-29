# ADR 010 Phase 2 authentication and posting implementation

Date: 29 October 2026. Status: Accepted implementation details within ADR-002/003/005/008 and the approved SRS; no provider or architecture replacement.

FastAPI owns identity. Access JWTs use HS256 with explicit issuer, audience, key IDs and ten-minute maximum lifetime; PostgreSQL families provide immediate revocation and seven-day absolute refresh expiry. Privileged idle expiry is fifteen minutes. Secure HttpOnly host-only cookies, family-bound CSRF and an exact Origin allowlist protect browser mutations. SameSite is a deployment-profile setting; cross-site browser acceptance remains a release gate.

Store refresh/action/recovery token hashes, preserve used refresh records for replay detection, and lock families before consuming refresh values. Revocation on detected replay commits before the error is returned. Use a dedicated Fernet key for TOTP secrets; accepted adjacent-step counters are consumed once. Argon2id follows the SRS parameters and a two-operation process bulkhead. PostgreSQL-backed auth buckets supply distributed limits and bounded backoff without a permanent account lockout. Mailpit provides real local SMTP; hosted delivery is a separate adapter/configuration decision.

Use one SQLAlchemy pool and transaction-local authenticated user/business context. The migration identity owns the app schema and is checked at migration time; runtime has only explicit table/column/function grants. Forced RLS and composite references supplement server membership checks. Runtime may update the business version column to acquire row locks, but cannot update business currency/policy columns directly.

Draft and posted journals share durable identity, with draft-only runtime mutation policies. Normal and draft posting invoke one private, security-definer PostgreSQL implementation through narrow wrappers. Posted records remain immutable, including their source, period, policy and approval references. Draft transitions retain their operation identity. Reversals are new linked journals rather than edits to posted history; the read model derives the reversed state and link. Report queries exclude drafts.

This foundation preserves the modular monolith and future production AWS/outbox path. It does not add a broker, provider auth, public SDK or mobile application. Actual subledger source adapters and complete cash/accrual product reporting are Phase 3 work; the reconciliation primitive requires complete authoritative source input.

## Audit amendment

Migration 0020 binds sessions to the password generation actually verified by login. A database trigger increments the user's generation when the password hash changes; access and refresh reject sessions from an older generation, including those inserted after a concurrent reset's revocation query. This adds no token claims or provider dependency. The same migration rejects new reversals whose accounting date precedes the original journal. Existing posted records remain immutable. See [the Phase 2–3 audit](../phase-2-3-audit.md) for regressions and verification.
