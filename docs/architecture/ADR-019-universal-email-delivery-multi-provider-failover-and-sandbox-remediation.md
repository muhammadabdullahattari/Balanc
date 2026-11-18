# ADR-019: Universal Email Delivery, Multi-Provider HTTPS Failover, and Third-Party Sandbox Remediation

Status: Accepted, November 2026.

## Context

Following the cloud deployment of LedgerPilot to Railway and Vercel (ADR-018), operational verification revealed a critical failure mode during end-to-end user onboarding and password reset flows:

1. **Failure Observation**:
   * Attempting to reset a password for `theab9098@gmail.com` via the Vercel staging frontend (`https://balanc-48v9yo9n5-abd-c462.vercel.app`) failed with:
     ```text
     We couldn't send the email. Please try again shortly or contact support.
     Support reference 9ecf3322-9c74-454e-9169-241c1d0466ef
     ```
   * Live Railway network logs on the active `Balanc` container (`balanc-production.up.railway.app`) recorded:
     ```text
     2026-11-18 21:10:55 POST /api/v1/auth/reset-request 503 183ms
     ```
   * Earlier in the same session, `POST /api/v1/auth/verification-request` returned `202 Accepted` because `verification_delivery()` intentionally suppresses `ERR_MAIL_UNAVAILABLE` to allow user account retention, but the verification email never actually arrived in the recipient inbox.

2. **Root Cause Analysis (RCA)**:
   * **Root Cause A: Cloud Egress Firewall SMTP Blocking**:
     Railway (and many modern cloud PaaS providers on trial or hobby tiers) enforces a cloud-level egress firewall that silently drops or rejects outbound raw TCP connections on standard SMTP ports (`25`, `465`, `587`, `2525`). Outbound socket probes executed live from the container confirmed:
     ```json
     "port_probes": {
       "25": "BLOCKED: timed out",
       "465": "BLOCKED: timed out",
       "587": "BLOCKED: timed out",
       "2525": "BLOCKED: timed out",
       "443": "OPEN"
     }
     ```
     Consequently, direct SMTP dispatch to `smtp.gmail.com:587` with standard Google App Passwords failed with `OSError: [Errno 101] Network is unreachable`. All email delivery MUST travel over outbound HTTPS port `443`.
   * **Root Cause B: Resend Unverified Sandbox Restriction (HTTP 403)**:
     When configured with `RESEND_API_KEY`, testing against the registered Resend account owner (`gamehalludba@gmail.com`) succeeded with HTTP 200:
     ```json
     {"status": "ok", "delivered_to": "gamehalludba@gmail.com", "resend_enabled": true}
     ```
     However, when attempting delivery to any real prospective user or alternative account (such as `theab9098@gmail.com`), Resend returned `HTTP 403 Forbidden`:
     ```json
     {
       "statusCode": 403,
       "name": "validation_error",
       "message": "You can only send testing emails to your own email address (gamehalludba@gmail.com). To send emails to other recipients, please verify a domain at resend.com/domains, and change the `from` address to an email using this domain."
     }
     ```
     Resend enforces this anti-spam restriction on its shared test domain (`onboarding@resend.dev`). Without a verified custom DNS domain, Resend rejects all arbitrary recipient email addresses.
   * **Root Cause C: Rigid Single-Provider Pipeline Halting**:
     In earlier revisions of `backend/src/ledgerpilot/identity/mail.py`, if `RESEND_API_KEY` was present and failed with HTTP 403, it immediately raised `DomainError("ERR_MAIL_UNAVAILABLE", ...)` and terminated. It did not attempt alternative HTTPS providers (such as Brevo or SendGrid) even if keys were configured.

---

## Decisions

### 1. Resilient Multi-Provider HTTPS Failover Pipeline
In `backend/src/ledgerpilot/identity/mail.py`, email delivery is structured as a non-halting cascade across configured providers:
1. **Resend (HTTPS Port 443)**: Attempted first if `RESEND_API_KEY` is present. If HTTP 200/201 is returned, delivery completes immediately. If an error occurs (such as HTTP 403 sandbox restriction, 401 unauthorized, or 429 rate limit), the error is recorded, a warning is logged, and execution continues to the next provider.
2. **Brevo / Sendinblue (HTTPS Port 443)**: Attempted if `BREVO_API_KEY` is present. Dispatches via `POST https://api.brevo.com/v3/smtp/email`. If HTTP 200/201 is returned, delivery completes. If an error occurs, it is recorded and cascades.
3. **SendGrid (HTTPS Port 443)**: Attempted if `SENDGRID_API_KEY` is present. Dispatches via `POST https://api.sendgrid.com/v3/mail/send`. If HTTP 200/201/202 is returned, delivery completes.
4. **Standard SMTP**: Attempted if `settings.smtp_host` is defined. Retained for local development (in-process mock server) and unblocked cloud tiers.
5. **Aggregated Diagnostics**: If all attempted providers fail, the system aggregates errors from every provider into a clear diagnostic string and raises `DomainError("ERR_MAIL_UNAVAILABLE", aggregated_message, 503)`.

### 2. Zero-Domain Universal Delivery via Brevo
To allow instant transactional email delivery to **any recipient email address** without forcing the operator to own, purchase, or configure DNS records for a custom domain:
* **Why Brevo**: Brevo offers a free tier (300 emails/day) that allows sending to **any arbitrary recipient** globally. It only requires single-sender email verification (verifying that the operator owns their Gmail or personal email address by clicking a confirmation link sent to that inbox).
* **Configuration**:
  * Set `BREVO_API_KEY` in Railway environment variables.
  * Optionally set `BREVO_FROM` (defaults to `settings.smtp_from` or `noreply@balanc.local`).

### 3. Custom Domain Delivery via Resend
For production environments with an established domain name:
* Operators can add their domain under Resend Dashboard (`resend.com/domains`), add the DKIM/SPF TXT records to their DNS provider, and set `RESEND_FROM=LedgerPilot <noreply@yourdomain.com>`.
* Once verified, Resend lifts the sandbox restriction and delivers to all recipients.

### 4. Third-Party Sandbox Remediation & Error Transparency
When delivery fails because of a third-party testing sandbox (e.g. Resend's 403 restriction), the error message automatically detects the sandbox failure and appends an actionable operator remediation hint:
```text
[HINT: Resend test sandbox restriction active: onboarding@resend.dev only allows sending to the account owner. To deliver to '{recipient}', either configure BREVO_API_KEY in Railway for free domainless delivery, or verify a custom domain at resend.com/domains.]
```
This eliminates ambiguity in production logs and frontend support references.

### 5. In-Cloud Live Diagnostic Enhancement (`/health/test-email`)
The `/health/test-email` endpoint in `backend/src/ledgerpilot/main.py` is expanded to accept `resend_key`, `brevo_key`, and `sendgrid_key` query parameters, allowing operators to validate new API credentials live on the running container before setting them permanently in cloud platform variables.

---

## Consequences and Compatibility

1. **Unblocked Onboarding**: New users registering with arbitrary email addresses (e.g., `theab9098@gmail.com`) can receive verification and password-reset emails without requiring custom DNS domain setup.
2. **High Availability**: If Resend is throttled or sandbox-restricted, Brevo or SendGrid immediately delivers the email without user-visible 503 errors.
3. **CI/CD Invariance**: Local unit and integration tests continue to use the in-process mock SMTP server (`TestSMTPServer`) on `127.0.0.1:1025` without external network access.
4. **Security & Least Privilege**: Raw action tokens are never logged or exposed in error messages; only error metadata and provider HTTP response bodies are surfaced.
