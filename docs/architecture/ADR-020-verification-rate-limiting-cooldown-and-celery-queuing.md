# ADR-020: Verification Rate Limiting, Progressive Cooldown, and Celery Queuing

Status: Accepted, November 2026.

## Context

During operational testing of the user onboarding and verification lifecycle, a significant usability and delivery defect was identified:

1. **Defect Symptom**:
   * When an unverified user requests a second verification email (e.g. from a new tab, after page refresh, or if the original email was delayed), the server accepted the request with a generic `202 Accepted` but **did not actually issue or dispatch a second verification code**.
   * The root cause was an overly strict CSRF session binding check in `backend/src/ledgerpilot/identity/service.py:request_action()` (`not user.verification_csrf_hash or not secrets.compare_digest(user.verification_csrf_hash, digest(csrf))`). Because CSRF cookies rotate across sessions and browser tabs, any resend request from an updated browser session was silently discarded without issuing a token.

2. **Requirements**:
   * Resend must reliably dispatch verification emails to the user's registered inbox.
   * Total volume must be bounded to **a maximum of 3 emails within any 24-hour window**.
   * Progressive cooldown intervals must be enforced between successive requests:
     * **1st Email**: Dispatched immediately. Sets a **15-minute** cooldown before a 2nd email can be requested.
     * **2nd Email**: Permitted after 15 minutes. Sets a **30-minute** cooldown before a 3rd email can be requested.
     * **3rd Email**: Permitted after 30 minutes. Sets a **1-hour** cooldown, reaching the maximum 3 emails allowed per 24 hours.
     * **4th Email**: Rejected with HTTP 429 (`ERR_VERIFICATION_LIMIT`) until the 24-hour window from the first attempt expires.
   * State must be distributed and fast via **Redis caching**, with durable persistence in PostgreSQL.
   * Email delivery must run asynchronously via a **Celery task queue** with automatic retries and synchronous fallback.

---

## Decisions

### 1. Dual-Tier Cooldown & Rate Limiting (`identity.verification_limiter`)
* Implemented `evaluate_verification_cooldown()` in `backend/src/ledgerpilot/identity/verification_limiter.py`.
* **Redis Caching Tier**: Key `identity:verify_gap:{canonical_email}` caches `attempts`, `started_at`, and `blocked_until` with TTL matching the remaining 24-hour window. This provides sub-millisecond evaluation across distributed API replicas.
* **Database Durability Tier**: Columns `verification_attempts`, `verification_started_at`, and `verification_blocked_until` on `app.users` persist state durably across Redis restarts.
* **Progressive Cooldown Enforcement**:
  * Attempt 1: Gap of 15 minutes (`settings.verification_gap_1_seconds = 900`).
  * Attempt 2: Gap of 30 minutes (`settings.verification_gap_2_seconds = 1800`).
  * Attempt 3: Gap of 1 hour (`settings.verification_gap_3_seconds = 3600`).
  * If requested within a cooldown window, raises `DomainError("ERR_VERIFICATION_COOLDOWN", ..., 429, retry_after=wait_seconds)`.
  * If $\ge 3$ attempts within 24 hours, raises `DomainError("ERR_VERIFICATION_LIMIT", ..., 429, retry_after=wait_seconds)`.

### 2. Session Re-Binding on Legitimate Resend
* In `request_action()`, removed the silent failure condition.
* When a valid CSRF-authenticated request arrives for an unverified account, `user.verification_csrf_hash` is re-bound to the active browser's CSRF token (`digest(csrf)`).
* This ensures that when the user receives their new verification code, they can verify directly in their current browser window without receiving `ERR_VERIFICATION_SESSION`.

### 3. Celery Asynchronous Email Queuing
* Configured Celery application in `backend/src/ledgerpilot/celery_app.py` backed by Redis (`CELERY_BROKER_URL` / `RATE_REDIS_URL`).
* Created `@celery_app.task` `ledgerpilot.identity.send_verification_email` in `backend/src/ledgerpilot/identity/tasks.py` with 3 bounded retries and exponential backoff.
* In `service.py`, `dispatch_identity_email()` attempts Celery background execution (`task.delay(...)`) and gracefully falls back to direct delivery if the broker or worker is temporarily unavailable.

---

## Consequences and Compatibility

1. **Reliable Onboarding**: Legitimate users can safely request replacement verification codes without silent drops.
2. **Abuse & Spam Defense**: Malicious actors or aggressive automated clicks cannot flood inboxes or exhaust email quotas due to the strict 15m $\to$ 30m $\to$ 1h gaps and 3-email daily cap.
3. **Frontend Integration**: Emits HTTP 429 with standard `Retry-After` headers, which the frontend auth view consumes to render a live countdown timer.
4. **CI & Operational Stability**: Direct delivery fallback ensures automated test suites pass without requiring a standalone Celery worker daemon during testing.
