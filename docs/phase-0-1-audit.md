# Phase 0 and Phase 1 foundation audit

Reviewed 27 October 2026 against the approved SRS, implementation plan, standards and accepted ADRs. This review covers repository setup, configuration, local database privileges and CI definitions, not unimplemented product workflows.

## Completion assessment

Phase 0 local foundation checks pass. The formal exit remains open because a successful GitHub-hosted run of the current changes has not been verified. The Git remote is now configured. Branch protection, Vercel linking and AWS environment activation also remain unverified; no push or cloud deployment was performed.

Phase 1 is complete for the approved local configuration scope. PostgreSQL is healthy, credentials are separated, runtime and migration identities work, prohibited operations fail, and the approved session design is documented. This does not mean authentication or tenant isolation is implemented.

## Confirmed findings and corrections

1. **High — connection identity validation could be bypassed by URL query parameters.** SQLAlchemy accepted `?user=postgres` even when the URL authority named the restricted role. `config.py` now allows only scalar certificate settings (`sslmode`, `sslrootcert`) in the query. Regression tests reject user, password, host, database, service, options and repeated TLS arguments. Hosted TLS verification remains required. Runtime database grants must still be verified; configuration alone cannot prove a role is restricted.

2. **High — direct Compose bootstrap bypassed the Python credential checks.** SQL could alter an existing privileged role or collapse runtime and migration identities. `backend/db/init/01-roles.sql` now checks role separation, password separation/strength, privileged existing roles, memberships and schema ownership before changing anything. Five real PostgreSQL rejection cases pass, followed by normal grant verification. Invalid bootstrap raises an error and rolls back. Existing unexpected privilege drift is rejected for operator review rather than silently repaired.

3. **Medium — checking one environment file inherited ambient settings.** `Settings.model_validate` still used BaseSettings sources, allowing the shell or default file to fill missing values or invalidate unrelated settings. The operator checker now uses only supplied values and declared defaults. A regression test verifies both ambient override rejection and missing-secret failure. Normal API startup retains the intended environment precedence.

4. **High — release publishing did not depend on quality gates.** Both CI and release now call `checks.yml`. Publishing depends on backend tests, live database checks, frontend build and container build. The built image is passed as an artifact into publishing instead of rebuilding a potentially different image. Artifact retention is one day; delayed approval requires a complete rerun. Hosted execution is still pending.

5. **Medium — deployment gating conflicted with documented environment configuration.** AWS values were checked at job scheduling time although README placed them in the protected environment. An explicit repository `ENABLE_DEMO_DEPLOY` flag now controls scheduling; required environment values are checked inside the job. Only the repository default branch may deploy. Automatic cancellation is disabled so a newer push does not interrupt an active deployment. This is a demo pipeline, not the SRS production topology.

6. **Low — Docker context had no exclusion boundary.** Added a backend allowlist `.dockerignore` so local credentials, environments, caches and unrelated files are not sent as build inputs. The image still copies only the runtime source and package inputs.

7. **Documentation — progress evidence was stale.** The tracker still said no Git remote existed and the image had not been built. Version 2.6 records the current evidence and keeps hosted CI and deployment gates explicitly open.

## Architecture assessment

The implementation preserves ADR-001 through ADR-009: Python/FastAPI modular monolith, SQLAlchemy/psycopg, PostgreSQL source of truth, React/TypeScript/Vite web shell, separate operator credentials, and no premature services or broker. No Supabase Auth, native mobile application or task-engine architecture was introduced.

The private `app` schema is migration-owned. Runtime is not an administrator, owner or RLS-bypass role. Current default SELECT/INSERT/UPDATE grants are scaffolding, not accounting authorization. Phase 2 must narrow table/function grants, enforce immutable balanced journals, scope transactions to authenticated tenants and verify cross-tenant denial using the actual runtime connection. Default grants alone cannot deliver these properties.

Pool sizes and timeout settings currently define a budget; no API session factory exists yet. Phase 2 must apply these settings, avoid stacked pools, bound transactions, implement readiness separately from liveness, and test shutdown/recovery. A healthy scaffold container proves process startup, not database-backed feature readiness.

JWT access cookies, hashed rotating refresh tokens, CSRF/Origin checks, password/MFA recovery and revocation remain Phase 2 implementation gates. Storage, email, AI adapters and durable outbox workflows belong to their feature phases. Production load balancing, measured capacity, backups/restore, distributed limits, monitoring and AWS topology remain later gates. Nothing in this audit establishes capacity for ten million users or free production hosting.

## Verification evidence

- 38 pytest cases pass: 33 unit checks plus five opt-in real PostgreSQL bootstrap rejection tests.
- Ruff and strict mypy pass. Clean frontend npm install and TypeScript/Vite production build pass; npm reported zero known vulnerabilities.
- Bootstrap reapplies without deleting data. Runtime/migration connectivity, schema ownership, sequence use and SELECT/INSERT/UPDATE pass; DELETE/TRUNCATE/DDL/role escalation fail as intended after rejection tests.
- Rebuilt API image passes its health check, runs as UID 10001 and serves HTTP liveness and OpenAPI using synthetic test credentials. Temporary smoke container is removed.
- All three workflow definitions pass actionlint, including expressions and shell checks. This does not substitute for GitHub-hosted execution or real AWS/OIDC/SSM testing.
- The prior Phase 1 password-rotation drill is recorded in the implementation plan; it was not repeated unnecessarily during this audit.

Run database integration tests only with local Compose running: set `RUN_DB_BOOTSTRAP_TESTS=1`, then run `python -m pytest` from `backend`. CI enables these tests after starting its isolated PostgreSQL instance. Without the flag, these five cases explicitly skip.

## References checked

- [GitHub configuration variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
- [GitHub reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [Artifact download contract](https://github.com/actions/download-artifact)
- Repository SRS: `docs/srs/LedgerPilot_Complete_SRS_Updated.docx`; accepted architecture decisions: `docs/architecture/ADR_INDEX.md`.
