# Traffic-spike hardening

This change set addresses the highest-risk request paths from the 10,000-RPS
static audit. It is not a capacity certification. The current rollout must
remain bounded until the remaining work and load tests are complete.

## Status by audit item

| Finding | Status | Scope |
|---|---|---|
| C1 PostgreSQL-backed admission | Fixed in production configuration | Atomic Redis counters with expiry, bounded pool and fail-closed protection. Local and test profiles retain PostgreSQL compatibility. External edge abuse controls still require deployment configuration. |
| C2 Fixture accounts and privileged DB role | Fixed in application code | Removed API startup seeding and domain-based MFA bypass. Runtime rejects privileged DB roles. Existing fixture accounts and sessions must be revoked by the operator before rollout. |
| H1 Session and workspace contention | Deferred in part | Common workspace reads avoid session row locks when recent activity is present. Session touches are coalesced to one minute. Workspace write serialization is intentional and remains. |
| H2 QuickBooks request transactions | Deferred in part | Fetches finish before ledger writes, ambiguous income/expense selection fails review. Full source-reference mapping, bounded job staging and batch SQL remain. |
| H3 Stripe webhook | Fixed for duplicate delivery and event-loop blocking | Atomic inbox deduplication, scoped SQL and indexed customer lookup, with SQL off the event loop. A durable asynchronous worker, replay tooling and stronger same-second event ordering remain. |
| H4 Upload pressure and storage | Deferred in part | Per-process admission and body deadlines bound work. Streaming, shared private storage, replica-safe evidence recovery and shorter disk-related lock time remain. |
| H5 Connection and startup amplification | Fixed in code, sizing pending | One-time migrations are an operator release step. Per-process pools remain configurable. A measured deployment-wide connection budget is required. |
| H6 Identity SMTP and checkout stalls | Deferred in part | Integrated provider calls have a per-process concurrency bulkhead and breaker. SMTP outbox conversion and moving checkout I/O outside the transaction remain. |
| H7 File disposal atomicity | Deferred | The existing synchronous unlink and audit transaction still need durable execution intents and crash reconciliation that preserve hold locking. |
| M1 Report and export amplification | Deferred in part | Private dashboard computation cache implemented. Export and report job workflows, plus summary tables for deep ledger history, remain. |
| M2 Cross-tab refresh | Fixed where Web Locks is available | Same-origin tabs serialize refresh when supported. Other clients retain strict replay protection and may still need to sign back in after a refresh race. |
| M3 Readiness pool sharing | Fixed | Separate bounded probe pool. Configure platform liveness independently from dependency readiness. |
| M4 Saturation metrics | Deferred in part | Request, provider, overload and cache events are emitted. Exported pool wait, queue depth, lock wait and event-loop lag metrics and alert wiring remain. |
| L1 Rate bucket growth | Fixed on Redis | TTL bounds keys and blocked requests do not extend an existing block. The legacy local PostgreSQL counter has no new janitor. |

## Configuration and compatibility

Run the local Redis services with
`docker compose -f docker-compose.performance.yml up -d`.
They bind loopback ports 16379 (admission) and 16380 (reports). Set
`RATE_REDIS_URL` and `REPORT_CACHE_REDIS_URL` for the API, respectively.
Use private, authenticated, separate hosted instances. Admission must use a
non-evicting memory policy so eviction cannot reset throttles. Plain Redis
URLs are suitable for local loopback; use the provider's secured endpoint
in hosted infrastructure. Redis admission failure returns 503 rather than
shifting the spike to the financial database. Report cache failures fall back
to PostgreSQL, with a nonblocking advisory lock per cache key.

Dashboard keys are `ledgerpilot:v1:report:private:<scope digest>`.
Inputs include workspace, actor, report parameters, ledger version, and policy
version. Authorization still runs on hits. Version changes make prior entries
unreachable and TTL expires them. The default 10-second TTL plus short jitter
bounds staleness across dependencies not represented by ledger version. Save a
fresh audit report ID per request. Leave caching disabled where this staleness
is unacceptable. No payment, identity, retention eligibility, or webhook
decision is cached.

See `.env.example` for thresholds, timeouts and connection limits.
Each default API process can use 10 application connections and two probe
connections. The local PostgreSQL admission profile adds two more. Account
for workers and operator sessions before setting replica limits. Hot-workspace
budgets deliberately reject unapproved demand, including in load tests.

No endpoint was switched to 202 or given a new job ID. Production startup now
requires the restricted DB role and Redis admission. This is a deployment
configuration change. Request overload and slow uploaded bodies can return
503 and 408 respectively, before command execution.

## Release order

1. Verify and back up the database. Before admitting external traffic, revoke
   sessions for previously seeded test identities, disable or remove their
   privileged fixture memberships, and confirm no known fixture credential
   can sign in. Preserve required audit history. Keep fixture accounts in a
   disposable environment only.
2. Provision separate runtime and migration roles. For a database previously
   deployed with a single administrative role, inventory and migrate schema
   ownership, object privileges and RLS in a staging clone. Compare against
   a clean migration with restricted roles. Do not grant ownership, superuser
   or BYPASSRLS to the runtime role.
3. Provision admission Redis and optionally the private report cache. Apply
   Alembic revision 0031 once using the migration identity. Confirm the
   restricted role can exercise endpoints and signed webhook deliveries.
   Deploy an internal canary, verify migration readiness, then expand.
4. Disable report caching with TTL 0 if cache behavior regresses. To roll
   back application code, use a prior security-hardened build compatible with
   the restricted DB role and schema revision, or stop customer traffic.
   The older public fixture bootstrap is not an acceptable fallback.
   Keep additive schema changes during application rollback unless a tested,
   separately authorized downgrade is required.

## Verification and capacity

Use disposable PostgreSQL, Mailpit and Redis. The Redis integration test opts
in through `LEDGERPILOT_TEST_REDIS_URL`. The hosted service is not a load target.

The k6 harness in `scripts/load/p2-spike.js` takes a disposable loopback
`LOAD_ORIGIN`, `LEDGERPILOT_DISPOSABLE_TARGET=yes`, and `FIXTURES` pointing
to a private JSON array of `{business_id, access_token}` for verified disposable
members. Keep the file outside version control. Run `k6 run scripts/load/p2-spike.js`
with `LOAD_MODE=rps` or `LOAD_MODE=concurrency`. Defaults to a 10,000 target.
Start with a much smaller `LOAD_TARGET` and use enough accounts and workspaces
to distinguish allowed throughput from throttled traffic. The current harness
measures authenticated account-list reads. Also run separate mixed workloads
for dashboard fan-out, upload saturation, writes to one workspace, Redis
failure, slow providers, duplicate webhooks, and recovery after interruption.

Initial staging thresholds are p95 below 500 ms, p99 below 1 s, under 1%
unexpected failures and no dropped injected iterations on the distributed
read test. Track 429 and 503 separately. Track application and DB connection
waits, CPU, RSS, lock waits, Redis saturation and rate/cache events. Confirm
zero tenant leaks, duplicate postings, broken audit outcomes, or missing
source records. No 10,000-user/RPS capacity claim follows from unit or
integration checks.
