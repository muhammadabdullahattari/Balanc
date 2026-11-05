# Deployment audit remediation - 5 November 2026

Scope: A1-A7 in `system-audit-2026-11-05.md`. This remains the single-host pilot. No cloud deployment, commit or push is included.

## Implementation

- A1: Every candidate mounts host `/var/lib/ledgerpilot/evidence` at that same path, UID/GID 10001, mode 0700. The helper overrides EVIDENCE_STORAGE_ROOT. Rollback preserves the old container and its mount. Workers must use the same directory and encryption key. A mount is not a backup.
- A2: Docker health checks readiness. A serialized deployment starts the candidate on the inactive loopback port (8000/8001), verifies readiness, reloads the nginx upstream, and keeps the old process until a second readiness check. Failure/signal traps restore the prior route and container names and verify previous readiness. Unexpected leftover deployment containers block another attempt for inspection. This covers normal failures, not power loss/SIGKILL.
- A3: Scanner IO has one ten-second monotonic budget across connect, writes and buffered replies. All operations use the remaining time. Positive verdict, command-ID and response-size checks remain mandatory. DNS still needs a responsive private resolver.
- A4: Export sources reject more than 10,000 rows and more than 10 MiB of serialized source metadata before loading ORM objects. JSON encoding enforces remaining bytes incrementally. Originals retain 20-file/5 MiB limits; total uncompressed pack is capped at 10 MiB. Identity history is no longer silently cut off at 2,000 entries. This is bounded synchronous export, not large background jobs.
- A5/A6: Business GETs use REPEATABLE READ; export snapshot creation uses SERIALIZABLE plus an export/member-specific try-lock. Idempotency and the three-export quota remain enforced. Authentication/activity touch commits in a short transaction; snapshot authentication is then revalidated without a long session write lock. Financial command isolation is unchanged. Snapshot reads bypass business shared locks; exports no longer lock the business for archive generation. Concurrent changes can prevent artifact persistence and return safe 409; explicitly retry with the same idempotency key. No financial write is automatically retried. Permissions/revocation are evaluated at request/snapshot start.
- A7: Open evidence details poll while processing, stop at extracted/unsupported/rejected and do not poll in hidden tabs. Canonical transport and cancellation remain.

## EC2 setup and recovery

1. Install nginx, Docker, AWS CLI and flock. Install the root-owned executable helper at `/opt/ledgerpilot/deploy-api.sh`. Keep `/etc/ledgerpilot/api.env` root-owned mode 0600.
2. Copy `deploy/aws/ledgerpilot-upstream.conf` to `/etc/nginx/ledgerpilot-upstream.conf`; include it once inside nginx's http context. In the TLS API location use `proxy_pass http://ledgerpilot_api;`. Preserve Host and trusted forwarding headers. Keep ports 8000 AND 8001 private. FORWARDED_ALLOW_IPS must identify the proxy peer actually seen inside Docker, never an externally reachable wildcard.
3. Validate/reload nginx. Migrate any direct port-8000 proxy to the named upstream before using this helper.
4. If originals exist in an old container's writable layer, stop ingestion and migrate/hash-verify them into the host directory BEFORE replacement. An empty new mount does not contain historical evidence.
5. Workers must mount the same directory and use the same key. Verify real scanner/OCR access and upload, extraction, download, container replacement, download/hash comparison.
6. Back up PostgreSQL and encrypted originals together. For this pilot, pause ingestion/writes during the coordinated backup. Keep encryption/signing keys securely escrowed separately, and an encrypted off-host backup. Preserve ownership/modes on restore. Restore into an isolated host/database, verify evidence hashes and representative signed packs, then reopen traffic. A deployment pass does not prove restore success.
7. After host loss or SIGKILL, inspect the nginx upstream, candidate/current/previous container names, readiness and mounts before retrying. Never blindly remove an unexplained leftover.

## Verification boundary

Results: `contracts/deployment-audit-results.json`. Command-double tests verify deploy control flow, not real nginx/TLS/IAM. Real PostgreSQL and TCP tests cover snapshot behavior, quota rejection and hostile scanner timing. Real container replacement tests verify encrypted originals survive container removal.

Hosted gates remain: actual proxy handoff/rollback, invalid-database readiness, private ingress/trusted forwarding/cookies, off-host backup/restore, monitoring, manual accessibility/browser matrix, and workload/RSS/latency measurements on the selected EC2 size. Fresh npm and Python advisory scans on 5 November returned zero known vulnerabilities. No dependency was changed in this remediation. This is a point-in-time feed result; it supersedes the prior scan count without claiming that absent advisories prove security.


## Completed local verification

169 all-service backend tests passed with zero failures/errors/skips. Frontend: 41 component, nine transport, 11 Chromium and 18 real-API workflow checks passed; production build passed. Ruff, mypy (98 sources), OpenAPI (126 unchanged paths), requirement reference validation (104), and workflow actionlint passed. Six deployment control-flow scenarios pass with command doubles. Real containers prove encrypted-original survival across removal/replacement and 503 readiness/unhealthy Docker status while liveness remains 200 with an invalid database.

The 11,000,000-byte metadata rejection used 27,394 peak traced Python allocation bytes during the measured query. This does not measure whole-process RSS or production capacity. See the results JSON for source hashes and verification boundaries. No commit, push or cloud deployment was performed.
