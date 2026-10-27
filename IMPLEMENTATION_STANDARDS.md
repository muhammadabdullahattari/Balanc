# LedgerPilot Implementation Standards

These standards apply to code in this repository. Use the following authority order when sources disagree: the user's current request, the approved LedgerPilot SRS, accepted LedgerPilot ADRs, the LedgerPilot implementation plan, then existing implementation patterns. Reference files from other products are advisory only.

## Product and architecture boundaries

- P1 is the responsive web product. Do not create native mobile applications or silently bring P2–P4 capabilities into P1.
- Keep the backend as the SRS-defined FastAPI modular monolith, PostgreSQL as the financial source of truth, and React/TypeScript/Vite as the web client.
- Preserve the SRS identity service. A provider-auth replacement, new broker, data store, or application architecture requires an ADR and an SRS/contract update before implementation.
- Treat the SRS as the accounting and release-scope authority. Record meaningful decisions and requirement traceability in `docs/architecture/` and `docs/`.

## Engineering practices

- Inspect relevant code, contracts, dependencies, data flows, and existing checks before making a meaningful change.
- Do not invent project facts, requirements, accounting behavior, provider settings, or test results. Verify them from the SRS, accepted ADRs, repository, or authoritative documentation; clearly label any necessary assumption.
- Work collaboratively with the user. If a requirement, design choice, scope boundary, or expected behavior is unclear and could materially change the implementation, ask the user before implementing the dependent work. Continue independent, clearly scoped work when possible.
- Prefer focused, maintainable changes; reuse the canonical route, service, model, component, and client for each capability.
- Keep HTTP handling in routers, business workflows and accounting policy in services, persistence in repositories, and durable invariants in PostgreSQL constraints/triggers where appropriate.
- Use Pydantic schemas at API boundaries and SQLAlchemy models for persistence. Keep migrations versioned in Alembic.
- Validate untrusted input, handle errors explicitly, and never return stack traces, SQL details, or secrets to clients.
- Do not push to a remote or publish a release unless the user explicitly asks.

## Accounting and data integrity

- Store posted monetary amounts with exact decimal/numeric semantics; never use binary floating point for ledger arithmetic.
- Posted journal entries must balance, be atomic and idempotent, and remain immutable. Correct posted history with linked reversals or adjustments, never destructive edits.
- Reconcile receivable, payable, loan, tax, and other subledgers to their general-ledger control accounts.
- Respect the business's accounting basis, currency precision, timezone, fiscal periods, and period locks.
- Treat bank activity as evidence of cash movement. Unknown or ambiguous transactions stay in review; do not silently turn a deposit into revenue or a withdrawal into an expense, loan payment, tax, or transfer.
- Derive tenant scope from authenticated identity and verified business membership. Never trust a tenant identifier supplied only in a request body or header.

## Real behavior and test doubles

- Do not ship fake or static business behavior, fabricated financial values, placeholder success responses, or timer-based simulated operations.
- Test doubles may be used in isolated unit/component tests and during the explicitly planned pre-integration UI phase. They must implement the real contract, remain outside production builds, and be replaced before API-integration exit.
- Integration and deployed end-to-end checks use the real application, PostgreSQL, and configured services in isolated non-production environments.
- When a check cannot run, report the exact environmental blocker rather than implying it passed.

## Security and secrets

- Keep `.env` ignored and use `.env.example` only for names and clearly non-secret development defaults.
- Validate production configuration at startup; keep migration and runtime database credentials separate and least-privileged.
- Never expose database, signing, service-role, storage, or provider secrets to the browser or logs.
- Enforce authentication, authorization, tenant boundaries, CSRF protection, upload safety, idempotency, and audit recording on the server.
- Protect financial and personal data in logs, exports, files, error reports, and backups; redact secrets and minimize sensitive payloads.

## System architecture, scale, and operations

Treat the following as required design and delivery concerns. Apply them according to the approved LedgerPilot SRS, accepted ADRs, and the current phase. Do not add infrastructure or complexity before its phase or measured need; when an item is deferred, record the scope, trigger, and follow-up phase in the implementation plan or an ADR.

1. **Capacity planning and performance targets:** Define expected and peak requests per second, concurrent connections, throughput, latency percentiles, and acceptable error rates for each workload. Set measurable targets and validate them under representative load; do not claim a user or throughput capacity without evidence.
2. **Load balancing and horizontal scaling:** For production deployment, provide a reverse proxy/load balancer, multiple stateless API instances, health/readiness checks, graceful shutdown, and documented autoscaling triggers. Respect the SRS deployment topology and introduce scaling only after workload and cost are measured.
3. **Rate limits and abuse prevention:** Define distributed limits and burst behavior by IP, authenticated user, API key, tenant, and endpoint as applicable. Include temporary restriction behavior, safe client errors, and monitoring; limits must not permit cross-tenant interference.
4. **Database design and optimization:** Review indexes and query plans for critical paths; use bounded pagination and filters, controlled connection pooling, explicit transaction boundaries, and tests/inspection to prevent N+1 query behavior. Add read replicas only where consistency needs and measured read load justify them.
5. **Caching:** Document cacheable data, cache keys and tenant scope, TTLs, invalidation, cache-aside behavior, stampede prevention, Redis sizing/limits, and behavior when the cache is unavailable. Never treat cache contents as the financial source of truth; ensure cached data cannot cross tenant boundaries.
6. **Security architecture:** Cover authentication, authorization, tenant isolation, validation, secrets management, TLS, encryption at rest/in transit, audit records, and least-privilege identities. Verify controls in tests and deployment configuration, not documentation alone.
7. **Background processing:** Use the SRS-approved queue/runtime for long-running or scheduled work. Define bounded retries, backoff, dead-letter/replay handling, idempotency, scheduling, task leases/visibility, observability, and worker scaling. Do not introduce Celery or another broker if it conflicts with an accepted ADR; architecture changes require an ADR first.
8. **Resilience and fault tolerance:** Set explicit timeouts, bounded retries with backoff, circuit breakers and bulkheads where appropriate, graceful degradation, and dependency-failure behavior. Retries must not duplicate financial effects; all side-effecting work must be idempotent or safely deduplicated.
9. **Observability and threat detection:** Provide centralized structured logs, metrics, distributed traces, security alerts, anomaly signals, service dashboards, and incident response procedures according to the observability ADR and phase. Redact secrets and sensitive financial/personal data.
10. **API and data contracts:** Define resource models, API versioning, pagination/filter limits, request-size limits, idempotency, consistency expectations, and transaction guarantees. Preserve backward compatibility or document and test an intentional breaking change.
11. **Deployment and infrastructure:** Use containerization, CI/CD, infrastructure as code for repeatable environments, environment separation, explicit scaling policies, and safe rolling or otherwise reversible deployments. Do not add an IaC tool or cloud service outside the accepted architecture without documenting the decision.
12. **Disaster recovery and durability:** Define backup and replication policy, point-in-time recovery where supported, recovery time objective (RTO), recovery point objective (RPO), retention, access controls, and scheduled restore tests. A successful backup job alone does not prove recoverability.
13. **Testing and capacity verification:** Select relevant load, stress, spike, soak, failover, security, and concurrency tests. Test representative workflows and financial invariants at and beyond the agreed peak; record environment, dataset, results, bottlenecks, and limits. Do not run uncontrolled load against shared or production services.
14. **Cost and resource management:** Set CPU, memory, connection, storage, queue, cache, and request limits; define capacity thresholds, scaling budgets, and cost alerts. Track spend by environment/workload and document quota or free-tier constraints; free-tier availability is not a production capacity guarantee.
15. **Documentation and operations:** Maintain architecture decisions, dependency/service maps, capacity assumptions, scaling and alert procedures, dashboards, incident and recovery runbooks, and rollback steps. Update documentation when implementation or operational behavior changes.

These concerns are phase-gated: Phase 0 establishes the project and local verification baseline; later design, implementation, and deployment phases must add the concrete controls and evidence before their relevant exit gates. The SRS's future scale goals are not assumed achieved by the initial demo.

## Completion report

Before considering work complete, verify:

- [ ] Requested functionality is implemented.
- [ ] Project patterns and relevant edge cases were considered.
- [ ] Appropriate tests were added or updated and relevant tests were run, or the reason they could not run is stated.
- [ ] Relevant existing functionality was checked for regressions.
- [ ] No unrelated changes or exposed secrets were introduced.
- [ ] Nothing was pushed to the remote repository unless explicitly requested.

When reporting completion, briefly state what changed, tests and results, and any remaining concerns or limitations. Use this format:

**Changed:** Brief summary of the requested work.

**Checks:** Tests/checks run and results; state any checks not run and why.

**Limitations:** Remaining concerns, blockers, or `None`.

Do not describe an unmeasured capacity, unsupported accounting policy, unfinished phase, or free hosting profile as production-ready. Apply this checklist and report format to every implementation task in this repository.
