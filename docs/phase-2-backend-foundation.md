# Phase 2 backend foundation

**Audit follow-up:** [Phase 2–3 audit](phase-2-3-audit.md) records subsequent repairs: password-generation session checks prevent the reset/login race, malformed CSRF input is rejected safely, and journal reversal chronology is enforced in the API and database. Current migration/readiness head is 0020. Original phase-specific verification below is retained as historical evidence.

Implemented 29 October 2026 against FR-001–004, VAL-001–005/007 and SEC-001–003, the approved browser session contract and accepted ADRs. This record covers the local backend foundation; Phase 3 supplies sales, purchasing, parties, banking, reconciliation workflows and the remaining P1 feature APIs.

## Runtime and database

- FastAPI application factory and lifespan own one bounded SQLAlchemy pool per process, using psycopg directly rather than stacking driver and ORM pools. Defaults remain five persistent plus five overflow connections, a ten-second pool wait, five-second connection timeout and fifteen-second statement timeout. Transactions have bounded lock/idle timeouts and rollback on failures.
- Requests receive validated/generated UUID correlation IDs. Operational logs contain method, status, duration and correlation metadata, without bodies, cookies, JWTs, email tokens or connection URLs. Errors use a documented envelope without raw SQL/driver errors or validation input values.
- Bodies are bounded to 256 KiB and JSON to ten nesting levels. Unknown request fields, invalid money/date/timezone values and unsafe display text are rejected. Pagination uses bounded limits and keyset cursors; the GL is ordered by accounting date, journal ID and line position.
- Liveness is independent of PostgreSQL. Readiness verifies the actual restricted database identity and migration revision. Interactive documentation is enabled only in local/test profiles.
- Alembic uses a separate migration URL, verified role/schema ownership and no identity/host query overrides. Hosted migrations require certificate-verified TLS. Revisions `0001`–`0005` provide the frozen schema, guards, readiness grants, minimal locking permission, auth backoff and draft/provenance model. Destructive removal of financial history is refused.

The development database retains its existing named volume. Windows stopped allowing the old 55432 binding, so this workstation now uses **127.0.0.1:15432**. The helper updates both connection URLs and the Compose port together. Other machines and CI may use the template's 5432 port.

## Identity and sessions

- Verified email registration, one-use 15-minute verification/reset tokens, password login, reset-driven session revocation, logout and logout-all use real PostgreSQL state.
- Passwords follow the SRS NFC/15–128-character policy and explicit Argon2id parameters: 64 MiB, three iterations, one lane, a random 16-byte salt and 32-byte output. An offline, licensed SecLists snapshot blocks common credentials. It is a baseline, not an exhaustive breached-password database.
- Password operations have a two-operation process bulkhead, bounding active Argon2 working memory to approximately 128 MiB plus library/process overhead. Overload fails explicitly rather than creating an unbounded queue.
- Access JWTs use pinned HS256, required issuer/audience/session/expiry claims and key IDs. Access lifetime is ten minutes. Optional previous-key verification supports a controlled rollover window; session lookup remains mandatory on protected requests.
- Refresh tokens are opaque, hashed, retained for replay detection and rotated under a family lock. Reuse revokes the family durably even though the rejected request fails. Families expire absolutely after seven days. Privileged memberships enforce the SRS fifteen-minute idle limit.
- Access, refresh and CSRF cookies use Secure, HttpOnly, host-only `__Host-` names and `/` paths. CSRF cookies persist across browser restarts. SameSite defaults to Lax; a cross-site demo must explicitly select None and retain Secure/CSRF/Origin checks.
- TOTP secrets are encrypted with a dedicated Fernet key. Adjacent thirty-second steps tolerate transmission/clock skew; accepted counters cannot be reused. Ten high-entropy recovery codes are returned once and stored hashed. Recovery-code use revokes earlier sessions. Password reset does not bypass MFA.
- PostgreSQL-backed per-IP and per-identity limits commit independently of failed authentication. Temporary bounded backoff and Retry-After responses do not suspend accounts. Suspended users and revoked/expired sessions fail access and refresh checks.

Use the loopback-only Mailpit service for actual local SMTP delivery. No verification/reset token is returned by the registration/reset-request API. Local mail is synchronous and bounded; durable provider delivery/outbox adapters belong to their integration phase. Delivery failures return an explicit availability error and roll back identity changes.

## Businesses and authorization

Business creation requires verified identity and MFA. Each request verifies active durable membership before binding transaction-local business context. Forced RLS, restricted runtime credentials and composite tenant foreign keys provide additional isolation. Pooled connections do not retain transaction-local identity.

Owner, Accountant, Bookkeeper, Reviewer and Auditor roles are explicit. Owners do not gain posting or period-lock permission automatically. Invitations expire after 48 hours, bind to a verified canonical email and are consumed atomically. Existing sessions lose business access when membership is revoked. Support impersonation and administrative membership/ownership workflows remain in the later P1 feature work; no unrestricted support bypass is provided.

Business settings include base currency, precision, timezone, fiscal month, accounting basis and jurisdiction-policy history. Policy changes are prospective and versioned; reads resolve effective history without changing prior rows. The pilot explicitly supports USD, PKR, EUR, GBP, JPY, KWD, CAD, AUD and CHF. Adding currencies requires reviewed precision metadata. This configuration does not assert jurisdictional tax compliance.

## Accounting core

- Typed accounts support deactivation and optimistic versions. Fiscal periods cannot overlap and require an accountant to lock/unlock. Lock provenance and open/closing/locked state are persisted. A manual period lock is not a claim that bank reconciliation or the full close workflow has passed.
- Draft journals are durable, versioned and excluded from the GL and trial balance. Draft edits lock the business and enforce the expected draft version. Only an authorized posting transition can publish them.
- Normal and draft posting share one private PostgreSQL implementation. Public database wrappers verify posting permission, business version, idempotency fingerprint, open period, active scoped accounts, currency precision and exact balance. The whole effect commits atomically.
- Posted headers and lines are immutable; appending to a previously posted journal is blocked. Corrections are exact linked reversals, with duplicate reversal protection. The read model derives the original journal's reversed state and reversal link without changing its immutable posted row. Period/policy foreign keys, operation UUID, source reference, approver, posted timestamp and local book are recorded at posting.
- Amounts are exact NUMERIC(24,6) decimal strings. NaN, infinity, scientific notation, negative journal sides and ambiguous debit/credit pairs are rejected. Half-even rounding is explicit; allocation distributes rounding remainder deterministically and preserves the approved total.
- GL and trial-balance APIs use posted records. The trial balance presents net closing debit/credit balances and exposes gross movements separately; aggregate decimal calculations use an explicit sixty-digit context. Both APIs return currency/timezone/filter context. They do not pretend that a bank filter implements cash-basis income recognition.
- The control-account reconciliation service compares independently supplied, complete subledger totals with signed GL balances and refuses incomplete sources. Actual AR/AP/loan/tax adapters depend on Phase 3 source modules; no fabricated balances or empty-success adapters are shipped.
- Immutable audit events record identity/session, business, draft, account, period and posting actions in their corresponding transactions.

## Local commands

From `backend`, run `uv sync --locked --all-groups` and `uv run python -m ledgerpilot.local_setup prepare`. From the repository root start PostgreSQL with `docker compose up -d --wait postgres` and the mailbox with `docker compose -f docker-compose.auth.yml up -d`. Run `uv run alembic upgrade head` from `backend`, then start the API with `uv run uvicorn ledgerpilot.main:app --reload`.

The mailbox UI is at `http://127.0.0.1:8025`. Configure `AUTH_DELIVERY_ENABLED=true` for local identity mail. These local values are present in the workstation's ignored environment file; fresh setup defaults delivery off until the mailbox is available. Database reset commands continue to target only the PostgreSQL Compose project.

Browser authentication requires HTTPS and an explicitly allowed web/API-docs origin. API integration tests use HTTPS ASGI requests and inspect cookie attributes; real browser cookie/CORS behavior is a later UI-integration/deployed acceptance check, not implied by these tests. For programmatic tests first obtain `/api/v1/auth/csrf`, retain cookies, and send the returned value in `X-CSRF-Token` plus the allowed Origin for mutations.

## Secrets and operational boundaries

`MFA_ENCRYPTION_KEY` is newly generated locally and must be backed up privately before enrollment. Never substitute a new key for existing encrypted MFA factors. The helper's `MFA_KEY_INITIALIZED` marker refuses silent replacement of a missing initialized key. Restore from private backup; any planned rotation must re-encrypt factors and be audited. Signing and encryption keys remain separate.

JWT issuer, audience, key ID, optional previous signing key/ID and cookie SameSite setting are server configuration. SMTP host/port/from and optional credentials/TLS are server-only. Hosted mail requires TLS. No paid AI, Supabase Auth, public SDK, native application or cloud deployment was introduced.

Before hosted release, verify trusted HTTPS/browser behavior, provider delivery, cleanup/retention jobs, restore procedures, external configuration and measured limits. Local foundation checks do not establish production capacity or an availability guarantee.

## Verification

The suite creates a uniquely named disposable PostgreSQL database with the real migration/runtime roles, applies migrations twice, exercises real SMTP delivery and API flows, and removes only that temporary database. It does not reset the development database. Unit/property checks cover parsing, token validation, one-use TOTP, exact money, allocation and control tie-outs. Integration checks cover auth lifecycle, replay/reset/suspension/idle handling, tenant denial, RLS context reset, posting/concurrency, immutability, period locks, policy scheduling, ledger cursors and durable drafts.

Set `RUN_PHASE2_INTEGRATION=1` and `RUN_DB_BOOTSTRAP_TESTS=1` to include real-service tests, then run `uv run pytest`. Without those flags, service-dependent tests explicitly skip. CI now starts both isolated local services and enables the relevant tests. The final evidence and current phase status are recorded in the implementation tracker. Revision 0005 is introduced before product journal data; it deliberately refuses legacy posted rows with missing provenance rather than inventing their period, policy or approval. Such data requires a reviewed backfill before upgrade.

Final local acceptance on 29 October 2026: 73 tests passed, 86% coverage; Ruff, mypy (45 source files), all three workflow definitions, and the final API image smoke passed. The mailbox HTTP/SMTP check passed after separation into its own Compose project. The migration-generation template renders and compiles; empty upgrades and rollbacks refuse execution until reviewed implementations exist. No remote commit, push, hosted CI run or deployment was performed for this change.
