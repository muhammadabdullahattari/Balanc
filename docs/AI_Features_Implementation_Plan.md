# Balanc: ten differentiated AI features — implementation plan

Date: 27 November 2026. Status: implemented and verified in local and CI environments (migrations `0040` and `0042`, commit `c7b8858`); see [P3_P4_AI_and_Audit_Implementation_Record.md](P3_P4_AI_and_Audit_Implementation_Record.md) and [ADR-022](architecture/ADR-022-trustworthy-ai-langgraph-audit-and-ux-hardening.md). Hosted external provider credentials remain operator-configured per environment.

## Scope and relationship to existing plans

This is the detailed delivery contract for the ten AI features requested by the user. IDs AI-01 through AI-10 identify features; waves W0–W6 identify delivery order. Neither replaces engineering phases 0–12 or existing P3/P4 package IDs. Most application capabilities extend P3; P4 supplies separately measured enterprise capacity and regional operations. Local development can proceed before hosted acceptance.

Preserve the FastAPI modular monolith, PostgreSQL accounting source of truth, React/TypeScript frontend, canonical API client, audited approval flows and accepted ADRs. Reuse current implementations rather than building a separate AI ledger, posting service, notification system or scheduler. These features are differentiation hypotheses, not a claim that competitors lack equivalent products.

Current source anchors inspected for planning:

- `backend/src/ledgerpilot/reports/accountant_tools.py` and `frontend/src/features/book-question.ts`: supported accounting questions and follow-up rules; these are not an existing general LLM integration.
- `backend/src/ledgerpilot/documents/ocr_service.py`: local Tesseract/embedded PDF extraction; broader OCR and speech support require evaluation.
- `backend/src/ledgerpilot/banking/suggestions.py`, `reconciliation.py` and `shadow.py`: correction-derived suggestions, reconciliation and controlled posting.
- `backend/src/ledgerpilot/workspace/close_packages.py`, `benchmarks.py`, `p3_automation.py`: workpapers, evaluation and automation entry points. Reinspect current behavior before each implementation slice; labels and screens alone are not model-execution evidence.
- `backend/src/ledgerpilot/celery_app.py`: an existing Celery configuration; do not assume every durable job already uses it. Review job ownership against ADR-020 before adding workers.

## Architecture contract

Browser text/audio/documents -> authenticated FastAPI request -> authorized AI run -> bounded worker execution -> optional LangGraph orchestration -> permitted tools/retrieval -> deterministic validation -> cited response or reviewed proposal.

- LangGraph is proposed for durable investigations and human-intervention steps. Record an ADR with retry/replay, checkpoint storage, migration, recovery and job ownership before adoption. It is not a queue replacement or an authorization boundary.
- LangChain is optional for selected model/retrieval integrations. Avoid imposing it on existing services.
- A model adapter must support structured outputs, cancellation, deadlines, token/step budgets, version recording and safe provider errors. No required vendor is selected by this plan.
- Use read-only typed tools first: report snapshots, aging, candidate matching, document lookup, policy lookup and proposal preview. No arbitrary SQL, shell, unrestricted browser or unrestricted outbound HTTP tool.
- RAG retrieves authorized document/policy passages. Numerical totals come from canonical accounting services using Decimal. Store dates, basis, functional/transaction currencies and snapshot versions explicitly.
- Start with PostgreSQL full-text and metadata retrieval. Evaluate pgvector plus an embedding model when semantic retrieval improves the labeled retrieval set. Do not require a separate vector database initially.
- Every tool call reauthorizes the actor and business on execution and on resume. Graph/checkpoint IDs, document chunks, embeddings, cache keys, status endpoints and streamed events are tenant-scoped. Portfolio tools additionally validate each business membership.
- Treat documents, messages, transcripts and retrieved passages as untrusted content. They cannot change system instructions or grant permissions. Revocation, deletion and legal holds propagate to derived indexes according to approved policy.
- Prefer the existing durable job infrastructure. Persist run state, attempts, next retry and failures; bound retries with backoff/jitter; support cancellation and operator replay. Do not claim exactly-once execution: deduplicate side effects using durable operation keys.
- Financial changes pass through current preparation, role/segregation checks, period locks, approval and posting tools. Existing controlled bank-rule posting remains governed by its approved policy. A new AI confidence score grants no posting/payment permission.
- Bind approval to proposal content/version, actor permissions and source versions; invalidate stale approval. On resume, repeat authorization and accounting validation. Never hold a database transaction open while waiting for a model or human.

## W0 — trustworthy AI foundation

Deliver before exposing model-generated recommendations:

1. Inventory actual routes, jobs, data models, approved policies and source evidence for the selected vertical slice. Record baseline behavior and regressions.
2. Define versioned request/result schemas: business, actor, period, basis, currencies, evidence, unsupported/insufficient-data outcomes and permitted actions.
3. Add the model adapter, typed tool registry, run lifecycle and durable job integration. Stream progress and expose bounded, paginated run/status/history APIs.
4. Persist auditable run metadata, model/prompt/tool versions, evidence references, snapshot hash/version, usage, latency and observed/estimated cost with units and provenance. Avoid logging secrets or unnecessary raw financial content.
5. Establish a dataset registry: synthetic development cases plus separately approved representative de-identified cases. Separate supplied predictions from actual model executions.
6. Replace or explicitly label predefined P3 benchmark figures as simulated; production UI must not present them as measured accuracy, confidence or savings.
7. Add feature flags, per-business concurrency/budgets, rate limits, timeouts, backpressure, unavailable-model fallback and operator pause. Existing manual workflows remain usable.
8. Establish evaluation gates and approval for model promotion. Thresholds must be configured and reviewed before a release; this plan does not invent accuracy or capacity results.

Exit: real model invocation can be traced to an authorized tool and valid citation; restart/retry, prompt injection, cross-business access and model outage tests pass. No financial write from free-form model output.

## Feature delivery contracts

### AI-01 — reconciliation detective

Extends P3-I/J/H and existing banking reconciliation. Delivered in W2.

- Compare bounded candidate sets across statements, bank rows, invoices, receipts, payments, credits, transfers, fees and settlement reports.
- Separate possible duplicate import, legitimate repeated charge, partial settlement, bundled payment, fee and timing differences. Show alternatives or request evidence when ambiguous.
- Deterministic services verify sums, currency, account eligibility and already-consumed allocations; LLM explains verified findings.
- UI: investigation workspace with source comparison, candidate explanation, missing evidence, proposal and approval handoff. Show running, canceled, unsupported, stale and failed states.
- Inputs required: labeled reconciliation cases, available settlement records, supported matching boundaries and accountant-reviewed expected allocations.
- Acceptance: bundled/partial/fee/duplicate/FX cases, locked periods, concurrency, retry and tenant denial. Preview has no ledger effect. Approved correction uses the canonical path and remains auditable/reversible where applicable.
- Measure: candidate precision/recall, accepted resolution rate, false matches, review time and abstention.

### AI-02 — financial impact preview

Extends P3-I and current journal preparation. Delivered in W1.

- Calculate before/after changes for affected account balances and supported P&L, balance-sheet, AR/AP and cash views. State date, basis, currencies and exclusions.
- Trace affected reports/workpapers and mark their dependency versions. Do not claim tax liability changes without a supported tax calculation.
- UI: comparison, journal lines, explanation, source links and existing approval action.
- Inputs required: approved journal/report cases and explicit supported report impacts.
- Acceptance: debit/credit equality, exact rounding, unchanged cash for noncash reclassifications, stale-state rejection, period locks, reversal and no mutation during preview.
- Measure: exact agreement with canonical calculations and reviewer comprehension; do not use LLM arithmetic.

### AI-03 — closed-loop client clarification

Extends P3-B/H. Delivered in W3 after AI-01/04.

- Convert unresolved exceptions into specific, deduplicated client requests; inspect replies and uploaded documents for account, period, required pages and relevance.
- Ask only for remaining information, attach responses to the originating exception, stop on resolution/opt-out and escalate disputes.
- Reuse approved channel adapters, consent, fallback order, quiet hours, templates and send history. Start with a configured email sandbox; other channels retain their provider gates.
- UI: accountant preview, client request/upload view, reply history, validation feedback and escalation ownership.
- Inputs required: communication permissions/templates, synthetic or approved conversation samples and per-document completeness rules.
- Acceptance: wrong/partial evidence, duplicate replies, late delivery, state changes before send, channel failure, opt-out, signed callbacks and prompt injection. No external messages are sent merely by creating this plan.
- Measure: requests per resolved exception, resolution time, incorrect closure and unnecessary reminders.

### AI-04 — approved business accounting memory

Extends P3-F2 and existing correction suggestions. Delivered in W1.

- Store structured, approved decisions with applicability, effective dates, evidence, author/reviewer, versions and revocation history.
- Retrieve comparable cases; distinguish a past example from a binding policy. Resolve conflicting/expired rules by requesting review.
- UI: inspect evidence, approve a suggestion, correct, supersede and revoke. Do not automatically turn rejected suggestions into policy.
- Inputs required: accountant-approved sample policies, precedence rules and authorized policy approvers. Do not treat IFRS and US GAAP as one interchangeable policy.
- Acceptance: conflicting/effective-date decisions, deletion/index invalidation, tenant isolation and revoked access; new decisions do not rewrite posted history.
- Measure: suggestion acceptance, incorrect policy application and retrieval relevance.

### AI-05 — continuous close assistant

Extends P3-E/H and current close packages. Delivered in W4.

- Build a dependency-aware readiness view from unresolved reconciliations, missing evidence, schedules, workpapers and required independent reviews.
- Detect candidate recurring omissions using explicit historical windows. Explain blockers and assign next actions; generate draft workpaper narratives with citations.
- UI: close timeline, blockers, owners, dependency drill-through and independent sign-offs. AI cannot sign for an accountant or guarantee correctness.
- Inputs required: approved close checklist, materiality configuration, deadlines and segregation-of-duties rules.
- Acceptance: missing periods, partially completed schedules, closed periods, late evidence, changed source snapshots and independent approver checks.
- Measure: preparation time, blocker resolution, reviewer-accepted findings and false-positive workload.

### AI-06 — multilingual voice accounting

Extends P3-C/F2. Delivered in W5.

- Support browser audio recording/upload, playback and correctable transcript, then use the same accounting tools and context as text requests.
- Extract intent and highlight uncertain names, amounts, dates and currency. Require explicit confirmation of critical fields before proposal preparation.
- Optional TTS reads verified answers and confirmation text; show the same cited text on screen. Speech alone does not authorize financial execution.
- Begin with a tested English/Urdu/mixed-language matrix; unsupported/noisy audio enters clarification. Do not promise every language.
- Inputs required: licensed speech/TTS candidates, representative consented or synthetic audio, target devices/languages, duration/size limits and approved audio retention treatment.
- Acceptance: accents/code switching, number/name errors, silence, interruptions, malicious transcripts, upload limits, mobile browsers, keyboard access and model outage. Verify financial entity accuracy in addition to word error rate.
- Measure: entity accuracy, correction burden, transcription latency, completion rate and spoken number pronunciation.

### AI-07 — contract/invoice/payment verification

Extends P3-F1/I/J. Delivered in W5 after AI-04 and document retrieval.

- Extract cited prices, effective dates, milestones, service periods, credits and renewal clauses from supported contracts.
- Compare invoice lines and existing payments using deterministic rules; propose questions and reviewed schedules. Distinguish extracted clauses from inferred accounting treatment.
- UI: clause-to-invoice comparison, discrepancy amounts, effective policy and proposed resolution.
- Inputs required: approved de-identified contracts/invoices, supported document/language matrix, pricing/discount/tax interpretation boundaries and accountant-reviewed schedule cases.
- Acceptance: amendments, conflicting versions, partial delivery, tier pricing, FX, missing PO/contract, duplicate payments and extraction uncertainty. No legal opinion or automatic treatment from an unsupported clause.
- Measure: clause/field extraction quality, supported discrepancy precision/recall and review time.

### AI-08 — action-based cash-flow scenarios

Extends P3-F2/I and current cash forecast. Delivered in W4.

- Convert user questions into explicit scenario parameters: payment delay, equipment purchase, cost change or collections assumptions.
- Compute baseline/alternative cash flows with canonical Decimal services. Distinguish scenario, deterministic forecast and statistically estimated prediction intervals.
- UI: editable assumptions, baseline comparison, affected weeks, evidence and save/share controls. No unapproved payment action.
- Inputs required: approved scenario definitions, forecast horizon, historical data coverage and calendar/currency rules. Predictive intervals need separate historical evaluation.
- Acceptance: empty history, overdue balances, negative cash, period boundaries, mixed currencies and unchanged baseline after simulation.
- Measure: calculation correctness and user task completion; prediction error only where predictive forecasts are actually run.

### AI-09 — accountant portfolio work planner

Extends P3-E/H. Delivered in W4 after AI-05.

- Rank authorized work by configured deadlines, materiality, dependency impact, severity and reviewer eligibility. Produce explainable recommendations and grouped work queues.
- Separate scheduling calculations from LLM summaries. Reuse existing assignment/history; persist optional saved views and planner preferences.
- UI: daily briefing, reason for order, assign/reassign, drill-through and explicit scope/currency labels. Never sum unlike currencies into an unlabeled value.
- Inputs required: business deadlines, assignment permissions, availability if used, materiality settings and approved ordering examples.
- Acceptance: empty/500-business portfolio, revoked memberships, independent reviewers, timezone changes, filtering before pagination and measured query budgets. Reuse realistic ledger fixtures.
- Measure: page/job p95, task completion, priority overrides and blocked-work reduction; current performance is not certified by this plan.

### AI-10 — verifiable AI evidence record

Extends audit/provenance and P3-F3/G. Delivered in W0/W1 and required by all features.

- Link each recommendation to source versions/passages, authorized report snapshots, deterministic calculation outputs, policy version, model/prompt/tool versions, uncertainty and execution outcome.
- Track proposal, review, approval, rejection and reversal actors/timestamps. Show a factual decision summary, not private model chain-of-thought.
- UI: expandable evidence record and scoped export; redact restricted content and record expired/unavailable source references honestly.
- Inputs required: audit access/export rules, approved retention coverage for AI-derived records and data-classification rules. Apply existing approved policy only to applicable classes; do not invent audio/embedding retention periods.
- Acceptance: citation validity, stale sources, tamper detection where implemented, revoked access, approved payload deletion, failed runs and approval-content mismatch. Internal audit records are not an external compliance certification.
- Measure: citation correctness, provenance completeness and reconstruction success.

## Delivery waves and dependencies

- **W0:** model/tool contracts, real execution/evaluation, jobs/security and AI-10 foundation.
- **W1:** AI-02 impact preview, AI-04 approved memory, AI-10 evidence UI. First complete browser vertical slice.
- **W2:** AI-01 reconciliation detective; reuse W1 previews and evidence.
- **W3:** AI-03 clarification and document completeness; integrate AI-01 and approved memory.
- **W4:** AI-05 close assistant, AI-08 scenarios, then AI-09 portfolio planner.
- **W5:** AI-06 voice and AI-07 contracts, each enabled only for its tested support matrix.
- **W6:** complete regression, accountant UAT, security/performance/cost evaluation, provider acceptance and gradual release. P4-scale trials remain separate.

Within every wave: inspect/reuse -> acceptance cases -> schema/migration if needed -> backend/tools/jobs -> API and DB tests -> frontend -> browser integration -> documentation/evidence. Do not redo already passing functionality. Re-estimate effort after W0 using actual model latency, dataset work and team availability; no delivery date is promised here.

## Proposed additions and configuration

Do not create these automatically from this plan. Exact file/module/table names and migrations follow repository inspection and the architecture decision.

- Backend responsibilities: model adapters, authorized tool registry, retrieval/index lifecycle, workflow/run state, evidence construction, evaluator and feature-specific services. Keep routers thin.
- Likely data responsibilities: run/step metadata, recommendations, source references, approved memory versions, dataset/evaluation runs and optional document chunks/embeddings. Reuse audit, report, approval and document records first.
- Frontend responsibilities: shared investigation progress, evidence drawer, impact comparison, clarification inbox, close planner, scenario editor, voice review and contract comparison. Reuse current navigation, forms, accessibility and API patterns.
- Configuration families: model adapter/endpoint/name/revision; optional API secret; embedding revision/dimensions; retrieval count and filters; run deadlines/steps/tokens/concurrency; feature flags and tenant budget; speech model/languages/limits; optional TTS/voice; dataset/baseline registry and evaluation thresholds. Define actual names and update `.env.example` during implementation. No secret values belong in documentation or browser code.
- Cache keys include tenant, authorization scope, report/source version, model/policy version and parameters. Use bounded TTLs, jitter/coalescing and explicit source-change invalidation; keep financial correctness independent of cache availability.

## Required resources and decisions

### Can start without external credentials

- Local PostgreSQL, existing Redis/job services, object/file storage adapters and isolated development database.
- Local model candidate, compatible inference runtime and enough measured RAM/VRAM/CPU/disk for the selected model/context/concurrency. A GPU is optional for initial experiments; establish usable latency before committing hardware.
- Synthetic accounting/documents/audio fixtures with reviewed expected outcomes. Synthetic data must be labeled and does not prove real-world performance.
- Existing FastAPI/React toolchain, Docker, unit/DB/browser suites and a development user for each relevant role. Never hardcode account credentials.

### User/accounting owner input required before dependent acceptance

- Initial user cohort, languages and documents; suggested pilot is reconciliation plus impact preview, followed by clarification.
- Business-specific accounting framework, approved policy examples, materiality, supported correction scenarios, close checklist and reviewer roles.
- Representative de-identified bank/invoice/contract cases with permission to use them and accountant-labeled outcomes. Public benchmark publication requires separate rights/approval.
- Decision on local-only inference versus optional hosted model, allowed data leaving the environment, data residency and operator budget. Hosted keys are required only if that route is selected.
- Consent/channel configuration for real client contact and licensed target voices if TTS is enabled. Sandbox development does not require production messaging access.
- Confirm how existing retention/holds apply to source audio, transcripts, conversation state, embeddings and run traces; approve any new schedule before destructive operations.
- Agreed evaluation thresholds, acceptable staleness, latency, per-run cost, escalation responsibility and release approvers.

### Software cost boundaries

LangGraph/LangChain, PostgreSQL and suitable open-source model/runtime candidates can support local development without paid API subscriptions. Check each pinned software/model/voice license. Examples to evaluate, not approved production selections: Qwen3-8B, Qwen3-Embedding-0.6B and faster-whisper. Piper's current engine is GPL-licensed; voice licenses differ. Self-hosting still consumes compute, storage and electricity; there is no free production scale guarantee.

## Verification and release gate

- Unit: extraction schemas, ambiguity/abstention, exact calculations, memory precedence, scenario parameters, tool limits and source binding.
- Integration against isolated PostgreSQL: tenant/RLS authorization, revocation, immutable records, transaction/approval versions, replay, cancellation, concurrent workers and migration fresh/upgrade paths.
- Retrieval/model: frozen datasets, measured invocation outputs, correct citations, malicious source documents, unsupported questions, source freshness and subgroup/language breakdowns. Never grade only with another LLM.
- Browser: complete role-specific workflows, keyboard/screen-reader behavior, responsive screens, mic denial, progress/reconnect, empty/error/outage states and stale approvals.
- Resilience: model/Redis/storage/provider failures, deadline exhaustion, queue backlog, resume after restart, step replay and no duplicated financial or messaging effect.
- Performance/cost: separately measure API latency, queue wait, model first response/completion, tool/query counts, tokens, CPU/RAM/VRAM and measured unit cost. Distinguish estimates, local measurements and hosted invoices.
- Final full regression: backend unit/integration, complete browser suites, frontend/component/API tests, Ruff, mypy, TypeScript, formatting, OpenAPI drift, frontend production build and relevant security checks. Record exact commands, final counts, dataset versions and failures after the final code change.
- Rollout: local synthetic -> approved representative offline evaluation -> staging/provider acceptance -> read-only cohort -> proposal workflows -> approved controlled actions. Feature pause and manual fallback must work at every stage.
- Mark a feature complete only when backend, UI, tested acceptance, documentation and applicable release gates have evidence. Distinguish development complete from production enabled. No push/deployment or external sending is authorized merely by this planning task.

## Implementation tracker

Updated 27 November 2026: W0 Foundation and AI-01 through AI-10 are implemented and verified end-to-end across PostgreSQL RLS migrations `0040_w0_ai_features_foundation.py` and `0042_clarification_delivery_status.py`, provider-agnostic Groq LLM/Whisper adapter (`backend/src/ledgerpilot/ai/adapter.py`), strictly read-only tenant-scoped tools (`backend/src/ledgerpilot/ai/tools.py`), FastAPI endpoints (`backend/src/ledgerpilot/ai/router.py`), plain-language accessible web UI (`frontend/src/features/ai-copilot.tsx`, `frontend/src/components/voice-recorder.ts`), LangGraph Financial Audit integration (`backend/src/ledgerpilot/audit/`, `0041_financial_audit_runs.py`), and automated unit/PostgreSQL/Playwright test suites (`backend/tests/test_ai_features_w0_to_w6.py`, `backend/tests/test_six_issues_regressions.py`, `frontend/tests/ai-copilot.test.tsx`, `frontend/tests/no-internal-labels.test.tsx`).

- [x] W0 foundation, operator pause/budget caps (`POST .../ai/controls`, `app.ai_operator_controls`), dataset evaluation provenance registry (`POST .../ai/datasets/evaluate`, `app.ai_evaluation_datasets`), and AI-10 run/provenance foundation (`0040_w0_ai_features_foundation.py`, `ai/adapter.py`, `ai/tools.py`).
- [x] AI-01 reconciliation detective (`POST /api/v1/businesses/{business_id}/ai/reconciliation-detective`, `app.ai_reconciliation_investigations`).
- [x] AI-02 financial impact preview (`POST /api/v1/businesses/{business_id}/ai/impact-preview`).
- [x] AI-03 closed-loop clarification with real confirmed email delivery (`deliver_workspace_email`, `0042_clarification_delivery_status.py`, `status='sent'`, `502 ERR_EMAIL_DELIVERY_FAILED` on provider failure, `POST /api/v1/businesses/{business_id}/ai/clarifications`, `POST .../reply`, `app.ai_client_clarifications`).
- [x] AI-04 approved business memory (`POST /api/v1/businesses/{business_id}/ai/memories`, `POST .../decision`, `app.ai_accounting_memories`).
- [x] AI-05 continuous close assistant (`POST /api/v1/businesses/{business_id}/ai/close-assistant`).
- [x] AI-06 multilingual voice accounting with `Permissions-Policy: microphone=(self)`, browser MIME negotiation, live recording timer & inline mic error states, Whisper `temperature=0`, deterministic SHA-256 `recording_id` reply caching (`localStorage` + PostgreSQL) with explicit **Regenerate reply**, and draft confirmation (`POST /api/v1/businesses/{business_id}/ai/voice-session`, `POST .../confirm`, `app.ai_voice_accounting_sessions` + optional LiveKit token).
- [x] AI-07 contract/invoice/payment verification (`POST /api/v1/businesses/{business_id}/ai/contract-verification`, `app.ai_contract_verifications`).
- [x] AI-08 cash-flow scenarios (`POST /api/v1/businesses/{business_id}/ai/cash-scenarios`, `app.ai_cash_scenarios`).
- [x] AI-09 portfolio work planner (`POST /api/v1/businesses/{business_id}/ai/portfolio-planner`).
- [x] AI-10 complete evidence UI, content-hash approval guard (`POST .../ai/runs/{run_id}/decision`), and redacted freshness-checked evidence export (`GET .../ai/runs/{run_id}/export`, `app.ai_runs`, `app.ai_tool_audit_logs`).
- [x] LangGraph Financial Audit engine (`0041_financial_audit_runs.py`, `backend/src/ledgerpilot/audit/`, `/financial-audit` UI) — see [financial-audit-langgraph.md](financial-audit-langgraph.md).
- [x] W6 final regression, plain-language UI hygiene gate (`no-internal-labels.test.tsx`), Playwright multi-viewport verification, and 10/10 CI gate (`c7b8858`, CI Run `45971565380`).

## Technical reference points

- LangGraph orchestration: https://docs.langchain.com/oss/python/langgraph/overview
- Human intervention/replay: https://docs.langchain.com/oss/python/langgraph/interrupts
- Retrieval concepts: https://docs.langchain.com/oss/python/deepagents/retrieval
- Model candidates: https://huggingface.co/Qwen/Qwen3-8B and https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- Speech runtime: https://github.com/SYSTRAN/faster-whisper
- Optional TTS: https://github.com/OHF-Voice/piper1-gpl

Recheck pinned versions, licenses and provider capabilities when implementation starts.
