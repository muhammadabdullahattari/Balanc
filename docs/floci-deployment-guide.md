# LedgerPilot Floci deployment guide

Status: implementation guidance based on supplied console screenshots, repository inspection and AWS references. Floci API, authentication, workload execution and durability have NOT been verified. No resource was created or image pushed by this document.

## 1. Verified screenshot details and corrections

The console supplies this repository URI:

`aws.meissasoft.com/027852729302/us-east-1/balanc`

Use that path, not the pasted guide's `api.aws.meissasoft.com/ledgerpilot-api`. The guide also contains conflicting account numbers. The screenshot path above is the authoritative value for these push instructions; confirm it with the console copy button.

The connected console backend URL is not proof of the native ECR/SQS/STS/SES endpoint or Docker authentication endpoint. Do not invent credentials such as floci/floci: the supplied identity guidance requires authenticated login and consent. Use a dedicated profile/session and the platform's actual login instructions. Registry login, native AWS API authentication and browser-console authentication can differ.

## 2. Image push from PowerShell

Run from D:/Balanc-ledger after required checks pass. These commands publish an image; they are instructions, not commands already executed. Because local code may be uncommitted, the example uses a unique candidate timestamp. For CI use the tested commit SHA and record the resulting image digest. Do not label uncommitted code with a misleading commit SHA.

```powershell
$flociRepository = 'aws.meissasoft.com/027852729302/us-east-1/balanc'
$flociTag = 'candidate-' + (Get-Date -Format 'yyyyMMddHHmmss')
docker build --platform linux/amd64 -f backend/Dockerfile -t "ledgerpilot-api:$flociTag" backend
if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
docker tag "ledgerpilot-api:$flociTag" "${flociRepository}:$flociTag"
if ($LASTEXITCODE -ne 0) { throw 'Image tagging failed' }
docker push "${flociRepository}:$flociTag"
if ($LASTEXITCODE -ne 0) { throw 'Image push failed: verify registry authentication and Docker registry routing' }
```

Confirm the tag and digest in ECR, then pull the image from a disposable runtime. Push success alone does not prove the runtime can pull/run it. If authentication is required, obtain the documented registry login method from Floci; do not guess an ECR password endpoint. Do not disable TLS verification to bypass certificate errors. Record tags/digests without registry credentials. The repository is MUTABLE in the screenshot: use unique tags and deploy by digest if supported.

Build the OCR image separately using `backend/Dockerfile.ocr` and the repository root as context (matching docker-compose.documents.yml). Give it a distinct ocr-prefixed tag if sharing the balanc repository, or use a separate repository. ClamAV is already pinned in the compose file. Verify the runtime can pull that image or explicitly mirror it with recorded provenance.

## 3. Compute and database pilot

Select an Ubuntu amd64 image compatible with the built image. The screenshot lists Ubuntu 22.04 amd64. Instance names alone do not establish actual hardware. Start with a verified real RAM budget around 12-16 GiB and adequate disk (e.g. 60 GiB as an initial pilot estimate), then measure. Existing ClamAV and OCR caps total 6 GiB, before API, workers, PostgreSQL and the OS; a micro-sized host is unsuitable for the complete combined workload.

Before launch, confirm how to access the machine. The console explicitly says stock AMIs have no sshd. A key pair alone is insufficient. Obtain a supported SSH/cloud-init or other remote-execution path; do not assume SSM works. Restrict administrative access to approved addresses, expose HTTPS, and keep PostgreSQL, scanner, OCR, SMTP and API candidate ports private. Require IMDS tokens if the runtime actually enforces them.

Verify Docker execution with a disposable container; verify restart/replacement persistence with a test file. Use Floci-managed PostgreSQL only after confirming real PostgreSQL version, connectivity, roles, extensions and restart durability. Otherwise run the existing restricted-role PostgreSQL profile on persistent storage for the pilot. The current local configuration is PostgreSQL, not Supabase Auth.

Deploy API, evidence worker, OCR, ClamAV, PostgreSQL and the selected email transport. Do not copy localhost service addresses into separate containers: configure actual private hostnames and ports. API and evidence worker must share the same private evidence directory and encryption key. Keep database and service credentials in runtime secret storage/root-only environment files, never images or frontend config. Apply migrations using the migration role before serving compatible application traffic; use the restricted role at runtime. Confirm schema 0022 and all readiness checks.

Follow deployment-audit-fixes.md for private persistent evidence, nginx upstream handoff, trusted proxy configuration, readiness, rollback and coordinated PostgreSQL/evidence/key recovery. The existing deploy/aws helper uses real ECR login and assumes Linux nginx and a filesystem mount. It cannot be pointed at Floci by changing a hostname alone; adapt its image acquisition and remote execution only after verification. Vercel must be able to reach the API through valid HTTPS. Update exact backend frontend-origin configuration, public frontend API origin and Google authorized origins for the actual deployed URLs.

## 4. SQS console settings for email

Create the dead-letter queue first, then the source queue. Initial settings below are pilot recommendations, not measured service limits.

| Setting | Email source queue | Email dead-letter queue |
|---|---|---|
| Name | ledgerpilot-demo-email | ledgerpilot-demo-email-dlq |
| Type | Standard | Standard |
| Visibility timeout | 120 seconds | 120 seconds |
| Delivery delay | 0 seconds | 0 seconds |
| Receive wait | 20 seconds | 20 seconds |
| Retention | 4 days | 14 days |
| Max message size | 64 KiB | 64 KiB |
| Encryption | Enable supported server-side encryption; verify behavior | Same |
| Redrive | Source sends to DLQ after 5 receives | Allow only this source queue |
| Access | Dedicated dispatcher sends; dedicated worker receives/deletes/changes visibility | Restrict operator replay/read access |

Do not leave the account-root SQS:* example as the application's credential policy. Verify enforcement with a second unauthorized identity; UI labels alone do not prove it. If policy/encryption enforcement is unavailable, restrict use to explicitly approved synthetic test data and record the gap before hosting users.

Use separate document-processing queues and DLQs if introducing the SQS document adapter. Do not mix slow OCR jobs with login email jobs. Start document visibility at 300 seconds only as a test setting, extend it during long work using heartbeats and set it from measured processing duration. Keep existing PostgreSQL jobs/leases authoritative; do not run a competing implementation that bypasses them.

Standard queues are appropriate here because no global ordering is required. FIFO deduplication does not guarantee exactly-once email sending or exactly-once external side effects. Consumers must remain idempotent and tolerate redelivery.

## 5. Application work required for email queueing

SQS does not deliver email. The current code in identity/mail.py uses SMTP; existing document jobs use PostgreSQL leases. Creating a queue does not connect either system automatically.

Required implementation, following ADR-006:

1. Add a versioned SQS adapter with explicit verified Floci endpoint/profile, bounded timeouts, long polling, visibility extension, send/receive/delete and safe diagnostics. Verify authentication rather than hardcoding mock keys. Proposed configuration names such as FLOCI_SQS_ENDPOINT_URL and FLOCI_EMAIL_QUEUE_URL are design names, not existing supported environment settings.
2. Atomically save pending identity state, current token and durable email outbox intent in PostgreSQL. Do not send an email for a transaction that can later roll back.
3. Publish only an event ID/type/version to SQS. Store necessary recipient/template and encrypted short-lived delivery material privately in PostgreSQL; a hashed verification token cannot be reconstructed for delivery. Never put plaintext tokens, passwords, full documents or unnecessary PII in queue bodies/logs.
4. The worker resolves and validates the durable job, current token generation, expiration and cancellation state. It skips expired/superseded codes, preserving browser binding, resend invalidation and abuse limits.
5. Send through a verified SMTP endpoint or a tested SES adapter. Record successful/ambiguous delivery durably and acknowledge with the current receipt handle only after completion. Use bounded backoff, DLQ and controlled audited replay. SMTP can succeed before a crash prevents recording the result; duplicate email remains possible without provider idempotency/status reconciliation. Do not claim exactly-once email delivery.
6. Integration tests must cover database rollback, broker outage, duplicate delivery, crash before/after send, stale token cancellation, worker restart, visibility extension and DLQ replay. Monitor oldest message age, outbox backlog, attempts, failures and expired jobs.

The SES Mailbox label does not establish internet delivery or even SMTP compatibility. Test a synthetic message and verify whether it is captured only or delivered externally. If capture-only, it can support a demo but personal inbox email will not arrive. Current hosted settings require TLS for enabled SMTP; do not weaken that validation merely to accommodate an unverified mailbox.

## 6. Safe SQS capability test

Once the actual native endpoint and login/profile are known: create a disposable queue, send a non-sensitive test ID, receive with long polling, leave it undeleted and verify redelivery after visibility expires, then delete using the latest receipt handle. Test DLQ redrive, persistence across runtime restart and unauthorized access. Copy generated QueueUrl/QueueArn values from the response; never construct them from the browser URL or guessed account ID. Use boto3's explicit endpoint_url only for a confirmed service endpoint. A console REST proxy path may not be an AWS SDK endpoint.

## 7. What is still needed

- Registry push result/tag/digest and whether login was required; runtime pull verification.
- Exact native SQS/SES/identity endpoint URLs, supported CLI login command and region. Share non-secret configuration only.
- Created queue URLs/ARNs and redrive configuration (no credentials or message bodies containing tokens).
- Compute access method, instance real CPU/RAM/disk and private/public addressing; HTTPS/API domain.
- PostgreSQL connection capabilities, persistent-volume/backup behavior and supported TLS; save credentials locally, not in chat.
- Mail transport: SMTP host/port/TLS or SES API endpoint, capture-only versus real external delivery.
- GitHub Actions authentication and reachability: hosted runner support, or a dedicated controlled runner if required. Browser login alone does not establish unattended deployment credentials.

## Official AWS references (contracts to verify against Floci)

- https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/FIFO-queues-exactly-once-processing.html
- https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/step-receive-delete-message.html
- https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html

These describe AWS behavior, not verified Floci parity.
