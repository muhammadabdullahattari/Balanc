# Task 1 — Staging readiness

Pass criteria stated before execution: identified and isolated staging target, PostgreSQL parity with the intended production topology, restricted API and worker roles, two API replicas and two workers, independent Redis and shared object storage, isolated email and Stripe, monitoring receiver, backup restored into a disposable database, migration 0034 verified, and a previous-build compatibility check.

This is discovery only. No endpoints, secrets, migrations, or production resources were touched.

### 2026-11-17T11:58:46.7152596+00:00

Command:

~~~powershell
git status --short; git log -5 --oneline; git worktree list
~~~

Output:

~~~text
?? docs/verification_evidence/
46739f8 feat(hardening): complete phase 6 fixture revocation, k6 scenario, and final report
abb8009 feat(hardening): implement Phase 5 observability and cleanup (M4, L1)
c492e13 feat(hardening): implement Phase 4 contention and capacity (H1, H5)
a86aa6b feat(hardening): implement Phase 3 storage and data safety (H4, H7)
9996961 feat(hardening): implement Phase 2 slow-work extraction (H3, H6, H2, M1)
D:/Balanc-ledger 46739f8 [main]
Native exit code: 0
~~~

### 2026-11-17T11:58:47.1145185+00:00

Command:

~~~powershell
Get-Command railway,vercel,gh,k6,docker -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
~~~

Output:

~~~text
vercel.ps1
k6.exe
docker.exe
Native exit code: 0
~~~

### 2026-11-17T11:58:51.9785979+00:00

Command:

~~~powershell
rg --files -g "*compose*" -g "*staging*" -g "*alertmanager*" -g "*railway*" -g "*prometheus*"
~~~

Output:

~~~text
docker-compose.documents.yml
docker-compose.auth.yml
docs\verification_evidence\01-staging-readiness.md
docker-compose.performance.yml
docker-compose.yml
Native exit code: 0
~~~

### 2026-11-17T11:58:52.1212277+00:00

Command:

~~~powershell
docker context show
~~~

Output:

~~~text
desktop-linux
Native exit code: 0
~~~

### 2026-11-17T11:58:52.8083029+00:00

Command:

~~~powershell
docker --context desktop-linux ps --format "{{.Names}} {{.Status}} {{.Ports}}" (local Docker Desktop; guarded by context and endpoint)
~~~

Output:

~~~text
failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
Native exit code: 1
~~~

### 2026-11-17T11:58:53.6571167+00:00

Command:

~~~powershell
rg -n "^      - alert:" deploy/prometheus/alerts.yml
~~~

Output:

~~~text
5:      - alert: DatabasePoolNearExhaustion
16:      - alert: JobQueueBacklogGrowth
27:      - alert: WorkerCrashLoop
38:      - alert: AdmissionRejectionSpike
49:      - alert: WorkspaceLockContentionSpike
Native exit code: 0
~~~

### 2026-11-17T11:58:53.9838272+00:00

Command:

~~~powershell
git show -s --format=%h HEAD; rg --files backend/migrations/versions | rg "003[1-4]"
~~~

Output:

~~~text
46739f8
backend/migrations/versions\0034_crash_safe_disposal.py
backend/migrations/versions\0033_webhook_and_balance_summaries.py
backend/migrations/versions\0032_background_jobs.py
backend/migrations/versions\0031_scoped_stripe_webhook.py
Native exit code: 0
~~~

### 2026-11-17T11:59:22.6336483+00:00

Command:
~~~powershell
rg -n 'version !=' backend/src/ledgerpilot/main.py
git show 5314fc3:backend/src/ledgerpilot/main.py | Select-String 'version !='
git show 5314fc3:backend/src/ledgerpilot/identity/service.py | Select-String 'email.endswith'
~~~

Output:
~~~text
308:                if version != "0034":
if not safe or version != "0030":
return bool(user and user.email.endswith(".local"))
~~~

## Status: Blocked

Only local Compose definitions were identified. The local Docker Desktop
daemon was unreachable. No authorized staging project, isolated service
configuration, or verified restorable snapshot was identified. Production
URLs in historical documentation were not used as substitutes.

Neither target role, database version or connection limit, API/worker
replica count, S3 isolation, provider sandbox isolation, backup
restore, nor migration state was verified on staging.

## Prerequisites to resume

- Staging project/environment ID, API and web origins, and a safe operator
  access path. Include a disposable staging workspace and a secure means
  of generating load-test sessions.
- Isolated PostgreSQL staging endpoint, production version and
  max_connections to match, runtime and migration role access, approved
  sanitized snapshot, and a separate restore destination.
- Private staging Redis admission/cache endpoints, S3-compatible bucket,
  and runtime access for two API replicas and two workers.
- Staging Prometheus and Alertmanager with an approved test receiver, and
  Mailpit or sandbox SMTP with controlled recipients.
- Staging-only Stripe test keys, price IDs, and webhook signing configuration
  in the secret store; no live payment or real mail credentials.
- Exact known-good rollback image digests. The reviewed older revision
  pins readiness to 0030 and has a domain-based fixture MFA shortcut;
  it cannot be treated as a compatible secure rollback target.

## Downstream status

Tasks 2 through 9: Not run. No load, soak, provider checkout,
fixture revocation, migration, alert, rollback, or release action was
executed in this verification attempt. Capacity is unmeasured.
