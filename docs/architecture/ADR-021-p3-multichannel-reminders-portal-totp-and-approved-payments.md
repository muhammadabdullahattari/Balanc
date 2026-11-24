# ADR-021: P3 Multichannel Reminders, RFC 6238 32-Character TOTP Supplier Portal Connectors, and Human-Approved Supplier Payments

## Status
Accepted (P3 branch `p3-features`)

## Context
Phase 3 (`P3`) introduces three operational automation capabilities that interact with external communication, vendor billing portals, and treasury settlement rails:
1. **Feature C (`P3-B` Multichannel Evidence & Bill Chasing)**: Delivering consent-based reminders across Official Meta WhatsApp Cloud API and Slack with email fallback.
2. **Feature D (`P3-A` Authorized Supplier Portal Invoice Connectors)**: Authenticating into read-only vendor billing sub-user accounts protected by Google Authenticator 2FA (`32-character Base32 TOTP seed`) or API connectors to ingest supplier invoices into OCR review drafts.
3. **Feature E (`P3-D` Human-Approved Supplier Payment Execution)**: Preparing supplier payment drafts and executing settlements only after explicit authorized human approval, out-of-band beneficiary verification, step-up authentication, dual approval on high-value thresholds, and emergency kill-switch checks.

## Decision
1. **Multichannel Reminders (`app.channel_reminder_preferences`, `app.channel_reminders`)**:
   - Store per-recipient consent (`whatsapp_opt_in`, `slack_opt_in`, `email_fallback`), channel priority order, quiet-hour windows, and attempt caps under forced PostgreSQL Row-Level Security (`FORCE ROW LEVEL SECURITY`).
   - Re-verify document resolution and evidence linkage immediately before every send (`skipped_resolved`) so reminders are never sent after settlement or evidence receipt.
   - Integrate directly with **Official Meta WhatsApp Cloud API** (`https://graph.facebook.com/v22.0/{phone_number_id}/messages`) and **Slack Webhooks / Bot API** (`#all-balanc`), with idempotent replay protection via `(business_id, idempotency_key)`.
2. **Authorized Supplier Portal Connectors (`app.supplier_portal_connections`, `app.supplier_portal_ingestions`)**:
   - Encrypt sub-user passwords and **32-character Base32 TOTP seeds** at rest using `Fernet(evidence_encryption_key)`.
   - Compute live 6-digit RFC 6238 TOTP codes (`HMAC-SHA1`, 30-second window) in memory during portal sync and store only the truncated SHA-256 hash (`totp_code_used_hash`) and provenance metadata.
   - Enforce content and external-ID duplicate guards (`content_sha256`, `(business_id, connection_id, external_invoice_id)`).
   - Route all ingested portal invoices into human-reviewable Purchase Bill Drafts (`ingested_to_draft`), prohibiting direct General Ledger posting from automated portal ingestion.
3. **Human-Approved Supplier Payment Execution (`app.supplier_payment_controls`, `app.supplier_beneficiaries`, `app.supplier_payment_executions`)**:
   - Require out-of-band verification (`out_of_band_verified = true`) on supplier bank beneficiaries and compute a cryptographic fingerprint (`account_fingerprint_sha256`) and version counter.
   - Automatically transition any unexecuted payment drafts (`prepared`, `first_approved`, `approved_ready`) to `invalidated` whenever a beneficiary's bank details change (Account-Takeover / Beneficiary-Swap Guard).
   - Restrict payment approval and release strictly to `owner` and `accountant` roles (`bookkeeper` and `viewer` roles are rejected with `403 ERR_FORBIDDEN`), require Step-Up 2FA verification, enforce dual approval when `amount >= dual_approval_threshold`, and check the workspace Emergency Kill Switch (`kill_switch_active`) before execution.
   - Support live Stripe Payment Rail settlement (`https://api.stripe.com/v1/payment_intents`) alongside built-in NACHA ACH and SWIFT MT103 wire remittance generation.

## Consequences
- All 7 new P3 tables in migration `0039_p3_reminders_portals_payments.py` enforce `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` scoped to `app.scope() AND app.has_access(business_id)`.
- Plaintext portal passwords and 32-character TOTP seeds are never persisted in the database or returned in list responses.
- AI and junior bookkeepers can prepare drafts and trigger reminders, but can never bypass human review for ledger posting or payment release.
