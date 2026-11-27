# ADR-022: Trustworthy AI Foundation (W0 & AI-01..AI-10), LangGraph Financial Audit, and Production UX/Media/Email Hardening

## Status
Accepted (27 November 2026 — `main` and `p3-features`)

## Context
Following the completion of P2 commercial workflows and P3 multichannel/portal/payment foundations (`ADR-021`), LedgerPilot required:
1. A provider-agnostic, strictly read-only AI layer implementing **W0 Foundation and AI-01 through AI-10** without granting autonomous posting or payment authority to any model.
2. A structured **LangGraph** workflow for comprehensive **Financial Audits** (monthly, yearly, emergency/on-demand, and auto-triggered on Balance Sheet generation and period close) combining deterministic `Decimal` rule tools, tenant-scoped RAG policy grounding, and plain-language Groq polishing.
3. Production hardening across browser media capture (`getUserMedia` / `MediaRecorder`), deterministic voice session persistence, IANA timezone selection, honest outbound email confirmation, and clean user-facing navigation free of internal ticket/phase jargon.

## Decision

### 1. Trustworthy AI Foundation & AI-01..AI-10 (`backend/src/ledgerpilot/ai/`, migrations `0040` & `0042`)
- **Provider-Agnostic Model Adapter (`ai/adapter.py`):** Isolate Groq (`llama-3.3-70b-versatile` and `whisper-large-v3-turbo`) behind `GroqModelAdapter` with env-only `GROQ_API_KEY`, bounded timeouts, exponential backoff, HTTP `429` handling, Whisper `temperature=0` + container MIME normalization, and `DeterministicFallbackAdapter` when unconfigured or offline.
- **Read-Only RLS-Scoped Tools (`ai/tools.py`):** Expose only parameterized `SELECT` tools executing inside the caller's authenticated PostgreSQL RLS transaction (`app.scope()`), capping rows/bytes, sanitizing untrusted strings against prompt injection (`sanitize_untrusted_text`), and recording non-sensitive invocation telemetry in `app.ai_tool_audit_logs`.
- **Verifiable Evidence & Operator Governance (`ai/router.py`):** Bind every recommendation (`app.ai_runs`) to a ledger `snapshot_hash`, `business_version`, `policy_version`, and `approval_content_hash` (`409 ERR_APPROVAL_HASH_MISMATCH` on tampered or stale approvals). Enforce per-business monthly USD/token caps and an operator pause switch (`app.ai_operator_controls`) that leaves manual accounting workflows unaffected.
- **Deterministic Voice Reply Persistence (`AI-06`):** Compute a deterministic SHA-256 `recording_id` per audio clip/transcript; cache generated replies by `(business_id, recording_id)` in both browser `localStorage` and PostgreSQL (`app.ai_voice_accounting_sessions`) so replies remain immutable until the user explicitly requests `regenerate=true`.

### 2. LangGraph Financial Audit Engine (`backend/src/ledgerpilot/audit/`, migration `0041`)
- **Graph Topology & Checkpointing:** Orchestrate audits via a 13-node `StateGraph(AuditState)` (`validate_request` → `load_data` → parallel fan-out across `retrieve_context`, `run_rules`, `reconcile`, `detect_anomalies` → `aggregate_findings` → conditional `flag_critical_findings` → `polish_report` → conditional `fallback_report` → `persist_and_return`) backed by `MemorySaver` thread checkpointing (`audit:{business_id}:{audit_id}`).
- **Deterministic Math & Numeric Hallucination Guard:** Perform all debit/credit equality checks, `Assets = Liabilities + Equity` verifications, opening/closing balance continuity checks, AR/AP/Tax subledger tie-outs, bank/payment rule checks, and duplicate/backdated/outlier anomaly scans strictly in Python `Decimal` `@tool` functions (`audit/tools.py`). `verify_no_invented_numbers()` rejects any Groq narrative introducing numbers absent from the deterministic payload and routes automatically to `fallback_report`.
- **Execution Modes & Savepoint Auto-Triggers:** Support synchronous runs (`201`) and `BackgroundJob`-queued emergency runs (`202`, `kind="financial_audit"`). Auto-trigger audits inside non-mutating savepoints (`db.begin_nested()` after `db.flush()`) on Balance Sheet generation, period lock, and close-package preparation so audit telemetry never masks domain validation errors.

### 3. Production UX, Media, Timezone, and Email Hardening
- **Same-Origin Microphone Policy:** Set `Permissions-Policy: microphone=(self)` (retaining `camera=()` on API responses) in `frontend/vercel.json` and `security_headers.py`, with inline accessible error states for denied, missing, busy, or insecure-origin microphones.
- **Fixed IANA Timezone Selection:** Replace free-text timezone inputs with a searchable selector derived from `Intl.supportedValuesOf('timeZone')` formatted with live `UTC±HH:MM` offsets and validated server-side via `zoneinfo.ZoneInfo`.
- **Confirmed Email Delivery Only (`identity/mail.py` & migration `0042`):** Route workspace clarifications and email reminders through `deliver_workspace_email()` (`Resend -> Brevo -> SendGrid -> Mail server`). Mark records `sent` **only** when confirmed by the provider; raise `502 ERR_EMAIL_DELIVERY_FAILED` with actionable guidance when unconfigured or rejected instead of recording fake `sent_sandbox` or unconfirmed `sent` states.
- **Scoped Navigation & Plain-Language UI:** Centralize automation page metadata in `frontend/src/features/automation-routes.ts`, hide cross-app hub tabs when mounted from standalone sidebar routes, and enforce zero internal ticket/phase/model jargon via `frontend/tests/no-internal-labels.test.tsx`.

## Consequences
- Schema head advances cleanly to `0042` with reversible migrations and forced tenant RLS on all `36` P3/P4/AI/Audit tables (`0039`–`0042`).
- AI and LangGraph workflows remain strictly advisory and read-only; every ledger write or payment execution still passes through existing human-approved accounting services and PostgreSQL invariants.
- Hosted cloud deployments (such as Railway containers where outbound TCP port `587` may be blocked) should configure an HTTPS email API key (`BREVO_API_KEY`, `RESEND_API_KEY`, or `SENDGRID_API_KEY`) so `deliver_workspace_email()` delivers over HTTPS `443`.
