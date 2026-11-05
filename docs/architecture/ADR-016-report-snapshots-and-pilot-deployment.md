# ADR-016: consistent report snapshots and durable pilot deployment

Status: Accepted within the user's audit-remediation scope, 5 November 2026.

## Context

Long report/export business and identity locks delay commands. The default READ COMMITTED transaction does not itself guarantee a multi-query snapshot. Container-local evidence is lost on replacement, and liveness is not readiness.

## Decision

Business GETs use request-scoped REPEATABLE READ. Export snapshot creation uses SERIALIZABLE and a member/business-scoped PostgreSQL advisory try-lock for quota/idempotency serialization. This does not change command transaction isolation. Authentication activity is committed in a separate short transaction and validated again within the read snapshot. No business/session write lock is held for report IO. The legacy shared-lock fallback remains for direct non-snapshot service callers.

Snapshot artifacts can encounter SQLSTATE 40001 when concurrent commands change a referenced business or export quota predicate. The transaction rolls back and returns the existing safe 409; the caller can explicitly retry with the same export idempotency key. No blind command retries. Authorization and revocation are evaluated at request/snapshot start. Reports may persist artifacts, so these are not database READ ONLY transactions.

The single-host deployment uses a persistent private host evidence directory, alternating private candidate ports and nginx upstream handoff after readiness. Preserve the old process until the replacement is ready; fail back to the previous upstream on ordinary errors. Keep immutable image references, restricted containers, root-only runtime env and operator-owned restore procedures.

## Consequences and compatibility

No schema/API change, provider, new broker or production-scale claim. Read-heavy traffic uses a brief extra authentication transaction and may return retryable conflicts. Pool sizing and hosted workload testing remain necessary. Evidence and its encryption key require coordinated off-host backups; persistent mounting alone is not disaster recovery. The helper requires the supplied nginx upstream include and both private ports. This is not universal zero-downtime ledger migration; expand/contract migrations still need separate compatibility review.

See `../deployment-audit-fixes.md` and its results record for checks and hosted gates.
