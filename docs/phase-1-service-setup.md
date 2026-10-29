# Phase 1 Service and Credential Setup

**Status:** Complete for the selected local service configuration, verified 27 October 2026.
**Scope:** Web SaaS development environment. Authentication endpoints, tenant schema, RLS and the DB session factory are Phase 2 work.

## Selected services and authentication

FastAPI owns identity and sessions. The user selected a JWT access token in the secure browser cookie plus an opaque refresh token stored as a hash, rotated and revocable on the server. The access token expires after 10 minutes. The [browser authentication contract](auth-session-contract.md) records the SRS cookie, CSRF, Origin, MFA and recovery requirements for Phase 2; selecting the design does not implement login.

Local Docker PostgreSQL 16 is the selected database. No Supabase project, hosted database or paid API key is required. The developer owns the local service and credentials. Storage, email, bank feeds, AI and telemetry providers are not provisioned in Phase 1; their integration phases must select and inventory them before use. Private document storage and scan-before-parse controls belong to upload implementation. No upload route is enabled by this setup.

## Local environment

`backend/src/ledgerpilot/local_setup.py` provides three operator commands, run from `backend`:

```powershell
uv run python -m ledgerpilot.local_setup prepare
uv run python -m ledgerpilot.local_setup check
uv run python -m ledgerpilot.local_setup verify
```

`prepare` creates or updates the ignored root `.env`, generates missing/placeholder secrets and aligns both URLs. It preserves unrelated settings and valid existing secrets. It prints names only and writes through a temporary file. It refuses hosted targets. `check` verifies configuration without contacting a service. `verify` uses real PostgreSQL connections, disposable probe tables and expected-denial checks. Probe tables are removed after verification. Validation failures print safe messages rather than raw database errors or values.

The existing local `.env` was updated with generated runtime/migration passwords, matching URLs and `POSTGRES_PORT=55432`. Its undersized bootstrap password was replaced before the first LedgerPilot database volume was initialized. The existing signing secret passed validation and was preserved. Other settings were preserved. No secret values are recorded here.

The template defaults to loopback port 5432. Another service already listens there on this workstation, so LedgerPilot uses 55432. To choose an available port, run `prepare --port 55432` before starting/recreating Compose; this changes the port and both URLs together. The container still listens on 5432 internally. The service is published only on `127.0.0.1`.

### Credential inventory

All selected credentials are local development values owned by the project developer. Generated values have no provider expiry; retire them with this environment or rotate them after suspected exposure. Do not copy them into demo/production.

| Names | Purpose and access | Storage and failure behavior |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | Local bootstrap administration and role maintenance | Ignored root `.env`, Compose DB container; absent from API Settings. Invalid values stop administration. |
| `APP_DB_USER`, `APP_DB_PASSWORD`, `DATABASE_URL` | Restricted application runtime identity | Ignored `.env`; runtime consumes the URL and expected username. Wrong role/invalid URL stops startup validation. |
| `MIGRATION_DB_USER`, `MIGRATION_DB_PASSWORD`, `MIGRATION_DATABASE_URL` | Own `app` schema and run migrations | Ignored local operator file and DB container initialization. Never pass the migration URL to a deployed API. |
| `SESSION_SIGNING_KEY` | Server signing secret for the Phase 2 session implementation | Ignored `.env`; minimum 32 bytes, no placeholder. Hosted environments need distinct protected secrets and session-aware key rotation. |
| `POSTGRES_DB`, `POSTGRES_PORT`, `FRONTEND_ORIGINS` | Non-secret DB identity/port and explicit origin allowlist | Safe examples committed. Reject invalid ports, wildcard/path origins and unsafe hosted configuration. |

These operator source values share one ignored local file for convenience. API Settings contains no administrator password or migration URL. Only runtime values belong in a deployment environment file; never pass the local operator file wholesale to the API container. Keep the Windows workspace ACL private; Unix temporary files use restrictive creation permissions. `.env` and credential temporary filenames are Git-ignored.

## Database privileges and connection policy

- The bootstrap administrator owns the local database; the migration role owns the private `app` schema. Application and migration logins have no superuser, database-creation, role-creation, replication, inheritance or RLS-bypass flags; verification rejects memberships in other roles.
- PUBLIC receives no database CONNECT/TEMP/CREATE grant from bootstrap, and no public-schema CREATE grant. Runtime receives CONNECT and USAGE on `app`. Migration-created tables default to runtime SELECT/INSERT/UPDATE, and sequences to USAGE/SELECT. Runtime DELETE, TRUNCATE, DDL and migration-role escalation are denied.
- Function EXECUTE is removed from the migrator's public defaults. Phase 2 grants needed functions and narrows posted-ledger permissions explicitly. Default grants are a setup baseline, not posted-ledger immutability or tenant isolation.
- Bootstrap runs transactionally and can be reapplied with `scripts/bootstrap-local-db.ps1`. The script validates local settings and applies current Compose environment values before SQL. It preserves the named volume; unexpected prior grants require review if verification fails.
- Local connections use loopback without DB TLS. Hosted profiles require `sslmode=verify-full` and HTTPS frontend origins. Configure a provider CA when needed; local checks are not hosted TLS evidence.
- Pool budget: `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`, `DB_POOL_TIMEOUT_SECONDS=10`, `DB_CONNECT_TIMEOUT_SECONDS=5`, `DB_STATEMENT_TIMEOUT_MS=15000`. These settings are validated; the Phase 2 SQLAlchemy session factory must apply them with pre-ping, explicit transactions and tenant-context reset. The operator verifier uses bounded direct connections.
- Account for `(pool size + overflow) x API/worker processes`, plus operator capacity, before scaling. Local limits depend on developer disk/RAM and Docker allocation; Phase 1 makes no production capacity or availability claim.

## Rotation and revocation

For normal local role-password rotation, stop API/workers, generate new independent app/migration passwords, update their private password variables and matching URLs together, then run `./scripts/bootstrap-local-db.ps1` from the root and `local_setup verify` from `backend`. Restart only after verification. Recreating the DB container applies current environment values but does not itself change initialized passwords; bootstrap performs that change.

The bootstrap administrator password requires an administrator connection and explicit PostgreSQL `ALTER ROLE`; changing `POSTGRES_PASSWORD` after initialization does not rotate it. Do not reset a volume to rotate credentials. For a compromised login, stop dependent processes, disable LOGIN or rotate its password through the administrator, terminate existing sessions deliberately, update configuration and verify old/new credentials. Existing sessions survive a password change until terminated. Runtime cannot escalate to administrator/migration roles.

For the verification drill, stop API/workers and run:

```powershell
uv run python -m ledgerpilot.local_setup verify --check-rotation
```

The drill temporarily changes the app password, proves old credentials are rejected and replacement works, restores the original in `finally`, then proves original connectivity again. It never prints values or changes `.env`. Do not interrupt it. If the process/machine stops before restoration, rerun bootstrap using the administrator and existing `.env`. Password rotation does not revoke browser sessions; Phase 2 implements signing-key rotation and session-family revocation.

If PostgreSQL fails, preserve its named volume, inspect health and rerun verification after recovery. Never substitute an administrator URL or pretend a check succeeded. Reset only disposable data with the explicit-confirmation reset command. Local Docker storage is not a backup; restore tests and funded recovery guarantees remain later phase gates.

## Verification and phase boundary

- [x] PostgreSQL starts healthy on the selected loopback port.
- [x] Runtime/migration logins authenticate with expected flags, memberships and schema ownership.
- [x] Migrator DDL, runtime SELECT/INSERT/UPDATE and sequence access work on disposable objects.
- [x] Runtime DELETE/TRUNCATE/ALTER/DROP/CREATE, role creation and SET ROLE escalation are rejected.
- [x] Password drill rejects old credentials, accepts replacement and restores original connectivity.
- [x] Reapplying bootstrap preserves the database and verification still passes.
- [x] Startup validation and redaction checks pass; credentials remain ignored.
- [x] CI definition prepares an isolated database, checks real grants/rotation and cleans its disposable volume. On 29 October 2026 the user confirmed the commit is done and GitHub checks passed; see the implementation tracker for evidence attribution.

Auth routes/session persistence, tenant tables/RLS, cross-tenant tests and the API pool are Phase 2 deliverables. Storage/email/AI keys are introduced in their integration phase. AWS/Vercel resources belong to deployment. The Phase 0 hosted-CI gate is closed on the user-confirmed result recorded in the implementation plan.

## References

- [PostgreSQL 16 grants](https://www.postgresql.org/docs/16/sql-grant.html)
- [PostgreSQL 16 default privileges](https://www.postgresql.org/docs/16/sql-alterdefaultprivileges.html)

## Foundation audit follow-up

The Phase 0/1 audit rejects connection query overrides, isolates file validation from ambient settings, and validates role/password collisions and existing schema ownership inside SQL bootstrap itself. Runtime URLs allow only scalar `sslmode` and `sslrootcert` query parameters. See [audit and verification](phase-0-1-audit.md).

## Foundation audit follow-up

The audit blocks connection query overrides, isolates selected-file validation, and rejects unsafe SQL bootstrap inputs before mutation. See [audit and verification](phase-0-1-audit.md).

## Phase 2 follow-up on 29 October 2026

The identity APIs, durable sessions, forced tenant RLS, migrations and bounded API pool are now implemented. The development database volume is preserved on 127.0.0.1:15432 after the previous binding became unavailable. The dedicated MFA key and local SMTP configuration are documented in [the Phase 2 record](phase-2-backend-foundation.md); earlier checks above are historical Phase 1 evidence.
