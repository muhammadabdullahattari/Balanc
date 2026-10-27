# ADR-008: Security and business multitenancy

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 3, 8, 12, 13

## Decision

- Use the SRS FastAPI-owned identity lifecycle: verified account, Argon2id password storage, MFA requirements for privileged roles, secure session cookies, refresh rotation, CSRF protections, throttling, and audited recovery.
- Model access through the SRS Owner, Accountant, Bookkeeper, Reviewer, Auditor, and Support capabilities. Enforce least privilege on every server operation; UI role checks are presentation only.
- Bind each request to an authenticated principal and an explicitly selected business membership. Resolve every record within that authorized business.
- Use tenant-scoped queries, composite foreign keys, and PostgreSQL row policies as layered controls. Do not trust `X-Tenant-Id`, body fields, or client-side state as authorization evidence.
- Support time-limited, scoped auditor/support access with purpose, approval, and audit evidence as defined by the SRS.
- Store public developer API credentials only in the later approved API/SDK phase; store hashes, reveal secrets once, and support immediate revocation.

## Phase 1 session decision

The user selected a short-lived JWT access token in the secure browser cookie and an opaque server-stored refresh token. Refresh values are hashed at rest, rotated and revocable. The SRS maximum access lifetime remains 10 minutes. `docs/auth-session-contract.md` records the boundary and Phase 2 acceptance evidence; selecting token formats does not implement the authentication lifecycle.

## Consequences

Every financial record and job has an auditable business boundary. Support and integrations cannot bypass normal authorization through privileged generic routes.
