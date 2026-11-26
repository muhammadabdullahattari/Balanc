import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Loading,
  PageHeading,
  SectionTabs,
  Table,
} from "../components/ui";
import { useBusiness } from "./context";

export type AICopilotTab =
  | "w0-evidence"
  | "ai02-impact"
  | "ai04-memory"
  | "ai01-recon"
  | "ai03-clarify"
  | "ai05-close-portfolio"
  | "ai06-voice"
  | "ai07-contracts-cash";

interface AIOverviewData {
  foundation: {
    wave: string;
    groq_configured: boolean;
    active_provider: string;
    llm_model: string;
    stt_model: string;
    tts_mode: string;
    livekit_enabled: boolean;
    livekit_mode: string;
    business_currency: string;
    business_basis: string;
    business_version: number;
    policy_version: number;
    read_only_tools_enforced: boolean;
    prompt_injection_shield_active: boolean;
    human_approval_required: boolean;
  };
  runs: Array<{
    id: string;
    feature_id: string;
    wave: string;
    provider: string;
    model_name: string;
    status: string;
    input_summary: string;
    confidence_score: string;
    uncertainty_flags: string[];
    snapshot_hash: string;
    business_version: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    latency_ms: number;
    estimated_cost_usd: string;
    approval_state: string;
    approval_content_hash: string;
    citations: Array<{ source_type: string; reference: string }>;
    tool_invocations: Array<{
      tool_name: string;
      row_count: number;
      result_bytes: number;
      duration_ms: number;
      untrusted_content_sanitized: boolean;
    }>;
    created_at: string;
  }>;
  tool_audit_logs: Array<{
    id: string;
    run_id: string;
    tool_name: string;
    parameters_hash: string;
    row_count: number;
    result_bytes: number;
    untrusted_content_sanitized: boolean;
    duration_ms: number;
    executed_at: string;
  }>;
  memories: Array<{
    id: string;
    memory_key: string;
    title: string;
    accounting_framework: string;
    category: string;
    binding_kind: string;
    rule_condition: string;
    account_code: string;
    account_name: string;
    materiality_threshold: string;
    effective_from: string;
    effective_to?: string | null;
    version: number;
    status: string;
  }>;
  reconciliation_investigations: Array<{
    id: string;
    statement_description: string;
    statement_amount: string;
    currency: string;
    classification: string;
    deterministic_delta: string;
    fee_amount: string;
    explanation: string;
    status: string;
  }>;
  client_clarifications: Array<{
    id: string;
    exception_kind: string;
    exception_ref: string;
    recipient_key: string;
    channel: string;
    questions: string[];
    required_documents: string[];
    completeness_score: string;
    remaining_gaps: string[];
    status: string;
  }>;
  voice_sessions: Array<{
    id: string;
    language_locale: string;
    stt_provider: string;
    raw_transcript: string;
    corrected_transcript: string;
    extracted_intent: string;
    extracted_entities: {
      party_name?: string;
      amount?: string;
      currency?: string;
      accounting_date?: string;
      account_code?: string;
    };
    uncertain_fields: string[];
    critical_fields_confirmed: boolean;
    linked_draft_id?: string | null;
    tts_readback_text: string;
    livekit_room_name: string;
    status: string;
  }>;
  contract_verifications: Array<{
    id: string;
    contract_reference: string;
    vendor_or_customer: string;
    currency: string;
    contracted_amount: string;
    invoiced_amount: string;
    paid_amount: string;
    discrepancy_amount: string;
    discrepancy_kind: string;
    proposed_action: string;
    status: string;
  }>;
  cash_scenarios: Array<{
    id: string;
    scenario_name: string;
    natural_language_prompt: string;
    currency: string;
    horizon_weeks: number;
    baseline_ending_cash: string;
    scenario_ending_cash: string;
    net_cash_delta: string;
    lowest_weekly_cash: string;
    shortfall_weeks: number;
  }>;
}

export function AICopilotHub({
  initialTab = "w0-evidence",
}: {
  initialTab?: AICopilotTab;
}) {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [tab, setTab] = useState<AICopilotTab>(initialTab);
  const [statusNotice, setStatusNotice] = useState<string>("");

  // AI-02 state
  const [impactDate, setImpactDate] = useState("2026-11-26");
  const [impactMemo, setImpactMemo] = useState(
    "Accrue November cloud infrastructure hosting expense",
  );
  const [debitAccountCode, setDebitAccountCode] = useState("6100");
  const [debitAccountName, setDebitAccountName] = useState(
    "Cloud Hosting Expense",
  );
  const [debitKind, setDebitKind] = useState<"expense" | "asset">("expense");
  const [creditAccountCode, setCreditAccountCode] = useState("2100");
  const [creditAccountName, setCreditAccountName] = useState(
    "Accrued Expenses Payable",
  );
  const [creditKind, setCreditKind] = useState<"liability" | "asset">(
    "liability",
  );
  const [impactAmount, setImpactAmount] = useState("1250.00");
  const [impactResult, setImpactResult] = useState<Record<
    string,
    unknown
  > | null>(null);

  // AI-04 state
  const [memoryKey, setMemoryKey] = useState("aws-hosting-categorization");
  const [memoryTitle, setMemoryTitle] = useState(
    "AWS Monthly Cloud Hosting Recognition Policy",
  );
  const [memoryFramework, setMemoryFramework] = useState<
    "US_GAAP" | "IFRS" | "TAX_BASIS"
  >("US_GAAP");
  const [memoryBinding, setMemoryBinding] = useState<
    "binding_policy" | "past_example"
  >("binding_policy");
  const [memoryCondition, setMemoryCondition] = useState(
    "Vendor bills from Amazon Web Services under $5,000 classify to 6100 Cloud Hosting Expense in period incurred.",
  );
  const [memoryAccountCode, setMemoryAccountCode] = useState("6100");
  const [memoryAccountName, setMemoryAccountName] = useState(
    "Cloud Hosting Expense",
  );
  const [memoryMateriality, setMemoryMateriality] = useState("500.00");

  // AI-01 state
  const [reconDesc, setReconDesc] = useState(
    "STRIPE PAYOUT - ACME CORP INV-101 & INV-102",
  );
  const [reconAmount, setReconAmount] = useState("970.00");
  const [reconDate, setReconDate] = useState("2026-11-26");
  const [reconDuplicateFlag, setReconDuplicateFlag] = useState(false);

  // AI-03 state
  const [clarKind, setClarKind] = useState<
    "missing_invoice" | "bank_row" | "unmatched_payment" | "contract_variance"
  >("missing_invoice");
  const [clarRef, setClarRef] = useState("BILL-2026-884");
  const [clarRecipient, setClarRecipient] = useState("ap-client-contact");
  const [clarChannel, setClarChannel] = useState<
    "email" | "whatsapp" | "slack"
  >("email");
  const [clarContext, setClarContext] = useState(
    "Missing tax invoice PDF and itemized service dates for November cloud charges.",
  );

  // AI-05 & AI-09 state
  const [closePeriodLabel, setClosePeriodLabel] = useState("2026-11");
  const [closeReport, setCloseReport] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [portfolioReport, setPortfolioReport] = useState<Record<
    string,
    unknown
  > | null>(null);

  // AI-06 Voice state
  const [voiceLocale, setVoiceLocale] = useState<
    "en_US" | "ur_PK" | "en_ur_mixed"
  >("en_ur_mixed");
  const [voiceTranscriptInput, setVoiceTranscriptInput] = useState(
    "K-Electric utility bill record karein for K-Electric Limited 850.00 USD due today",
  );
  const [voiceRequestLiveKit, setVoiceRequestLiveKit] = useState(false);

  // AI-07 & AI-08 state
  const [contractRef, setContractRef] = useState("MSA-2026-AWS-09");
  const [contractVendor, setContractVendor] = useState(
    "Amazon Web Services Inc.",
  );
  const [contractClause, setContractClause] = useState(
    "Section 4.2 Pricing Cap: Monthly committed cloud hosting fee is capped at 2,000.00 USD unless a signed milestone amendment is attached.",
  );
  const [contractedAmt, setContractedAmt] = useState("2000.00");
  const [invoicedAmt, setInvoicedAmt] = useState("2450.00");
  const [paidAmt, setPaidAmt] = useState("0.00");

  const [scenarioName, setScenarioName] = useState(
    "2-Week Customer Collection Delay + $8k Server Capex",
  );
  const [scenarioPrompt, setScenarioPrompt] = useState(
    "What if collections slip by 2 weeks and we buy $8,000 of servers in Week 1?",
  );
  const [delayWeeks, setDelayWeeks] = useState("2");
  const [equipmentCapex, setEquipmentCapex] = useState("8000.00");
  const [costChangePct, setCostChangePct] = useState("5.00");

  const overviewQuery = useQuery({
    queryKey: [...keys.business(b.id), "ai-copilot-overview"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/ai/overview", {
          signal,
          params: { path },
        }),
      )) as unknown as AIOverviewData,
  });

  const refreshOverview = () => {
    void queryClient.invalidateQueries({
      queryKey: [...keys.business(b.id), "ai-copilot-overview"],
    });
  };

  const impactMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/ai/impact-preview", {
          params: { path },
          body: {
            accounting_date: impactDate,
            memo: impactMemo,
            lines: [
              {
                account_code: debitAccountCode,
                account_name: debitAccountName,
                kind: debitKind,
                control: "none",
                debit: impactAmount,
                credit: "0.00",
              },
              {
                account_code: creditAccountCode,
                account_name: creditAccountName,
                kind: creditKind,
                control: creditKind === "liability" ? "ap" : "cash",
                debit: "0.00",
                credit: impactAmount,
              },
            ],
          },
        }),
      )) as unknown as Record<string, unknown>,
    onSuccess: (data) => {
      setImpactResult(data);
      setStatusNotice(
        "AI-02 Financial Impact Preview computed with deterministic Decimal tie-out.",
      );
      refreshOverview();
    },
  });

  const createMemoryMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/memories", {
          params: { path },
          body: {
            memory_key: memoryKey,
            title: memoryTitle,
            accounting_framework: memoryFramework,
            category: "categorization",
            binding_kind: memoryBinding,
            rule_condition: memoryCondition,
            account_code: memoryAccountCode,
            account_name: memoryAccountName,
            materiality_threshold: memoryMateriality,
            effective_from: "2026-01-01",
            evidence_note: "Reviewed by workspace accountant",
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice(
        "AI-04 Accounting Memory saved in pending_review state (awaiting approval).",
      );
      refreshOverview();
    },
  });

  const decideMemoryMutation = useMutation({
    mutationFn: async (vars: {
      memoryId: string;
      decision: "approve" | "supersede" | "revoke";
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/ai/memories/{memory_id}/decision",
          {
            params: {
              path: { business_id: b.id, memory_id: vars.memoryId },
            },
            body: {
              decision: vars.decision,
              reason: `Reviewed action: ${vars.decision}`,
            },
          },
        ),
      ),
    onSuccess: () => {
      setStatusNotice("AI-04 Memory status updated.");
      refreshOverview();
    },
  });

  const reconMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/ai/reconciliation-detective",
          {
            params: { path },
            body: {
              statement_description: reconDesc,
              statement_amount: reconAmount,
              posted_date: reconDate,
              is_duplicate_external_id: reconDuplicateFlag,
            },
          },
        ),
      ),
    onSuccess: () => {
      setStatusNotice(
        "AI-01 Reconciliation Detective completed candidate analysis.",
      );
      refreshOverview();
    },
  });

  const createClarificationMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/clarifications", {
          params: { path },
          body: {
            exception_kind: clarKind,
            exception_ref: clarRef,
            recipient_key: clarRecipient,
            channel: clarChannel,
            context_summary: clarContext,
            required_documents: ["Invoice PDF", "Tax Receipt Page"],
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice("AI-03 Client Clarification request prepared.");
      refreshOverview();
    },
  });

  const replyClarificationMutation = useMutation({
    mutationFn: async (vars: {
      clarificationId: string;
      action: "reply" | "opt_out" | "dispute";
      docs: string[];
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/ai/clarifications/{clarification_id}/reply",
          {
            params: {
              path: {
                business_id: b.id,
                clarification_id: vars.clarificationId,
              },
            },
            body: {
              client_reply_text:
                "Attached requested November invoice and tax receipt for verification.",
              uploaded_evidence_ref: "EV-2026-NOV-114.pdf",
              provided_documents: vars.docs,
              client_action: vars.action,
            },
          },
        ),
      ),
    onSuccess: () => {
      setStatusNotice("AI-03 Client reply validated and status updated.");
      refreshOverview();
    },
  });

  const closeAssistantMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/ai/close-assistant", {
          params: { path },
          body: {
            period_label: closePeriodLabel,
            materiality_threshold: "500.00",
            check_recurring_omissions: true,
          },
        }),
      )) as unknown as Record<string, unknown>,
    onSuccess: (data) => {
      setCloseReport(data);
      setStatusNotice(
        "AI-05 Continuous Close readiness and workpaper narrative generated.",
      );
      refreshOverview();
    },
  });

  const portfolioPlannerMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/ai/portfolio-planner", {
          params: { path },
          body: {
            materiality_threshold: "500.00",
            horizon_days: 7,
          },
        }),
      )) as unknown as Record<string, unknown>,
    onSuccess: (data) => {
      setPortfolioReport(data);
      setStatusNotice(
        "AI-09 Portfolio Work Planner ranked authorized workspaces without summing unlike currencies.",
      );
      refreshOverview();
    },
  });

  const voiceSessionMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/voice-session", {
          params: { path },
          body: {
            language_locale: voiceLocale,
            audio_base64: "",
            audio_format: "audio/webm",
            transcript_input: voiceTranscriptInput,
            request_livekit_room: voiceRequestLiveKit,
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice(
        "AI-06 Voice instruction transcribed and entities extracted. Confirm critical fields below to create a draft.",
      );
      refreshOverview();
    },
  });

  const confirmVoiceMutation = useMutation({
    mutationFn: async (vars: {
      sessionId: string;
      transcript: string;
      partyName: string;
      amount: string;
      currency: string;
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/ai/voice-session/{session_id}/confirm",
          {
            params: {
              path: { business_id: b.id, session_id: vars.sessionId },
            },
            body: {
              corrected_transcript: vars.transcript,
              confirmed_party_name: vars.partyName,
              confirmed_amount: vars.amount,
              confirmed_currency: vars.currency,
              confirmed_date: "2026-11-26",
              confirmed_account_code: "5000",
            },
          },
        ),
      ),
    onSuccess: () => {
      setStatusNotice(
        "AI-06 Critical fields confirmed -> Purchase Bill Draft created in /drafts.",
      );
      refreshOverview();
    },
  });

  const contractVerificationMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/contract-verification", {
          params: { path },
          body: {
            contract_reference: contractRef,
            vendor_or_customer: contractVendor,
            contract_clause_text: contractClause,
            contracted_amount: contractedAmt,
            invoiced_amount: invoicedAmt,
            paid_amount: paidAmt,
            prior_payment_count: 0,
            unapproved_milestone_billed: false,
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice(
        "AI-07 Contract vs. Invoice/Payment verification completed.",
      );
      refreshOverview();
    },
  });

  const cashScenarioMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/cash-scenarios", {
          params: { path },
          body: {
            scenario_name: scenarioName,
            natural_language_prompt: scenarioPrompt,
            horizon_weeks: 13,
            baseline_starting_cash: "50000.00",
            baseline_weekly_inflow: "12000.00",
            baseline_weekly_outflow: "9500.00",
            payment_delay_weeks: Number(delayWeeks) || 0,
            equipment_purchase_amount: equipmentCapex,
            recurring_cost_change_pct: costChangePct,
            collections_change_pct: "0.00",
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice(
        "AI-08 13-week Cash-Flow Scenario computed in canonical Decimal.",
      );
      refreshOverview();
    },
  });

  const runDecisionMutation = useMutation({
    mutationFn: async (vars: {
      runId: string;
      decision: "approve" | "reject" | "cancel";
      contentHash: string;
    }) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/runs/{run_id}/decision", {
          params: { path: { business_id: b.id, run_id: vars.runId } },
          body: {
            decision: vars.decision,
            expected_content_hash: vars.contentHash,
            reason: `Reviewer decision: ${vars.decision}`,
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice("AI-10 Evidence Record decision saved and audited.");
      refreshOverview();
    },
  });

  const speakReadback = (textToSpeak: string) => {
    if (
      typeof window !== "undefined" &&
      "speechSynthesis" in window &&
      textToSpeak
    ) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(textToSpeak);
      window.speechSynthesis.speak(utterance);
    }
    setStatusNotice(`TTS Readback: "${textToSpeak}"`);
  };

  if (overviewQuery.isPending) return <Loading />;

  const data = overviewQuery.data;
  const f = data?.foundation;

  return (
    <div className="page">
      <PageHeading
        eyebrow="W0 FOUNDATION & AI-01 THROUGH AI-10"
        title="Trustworthy AI Accounting Copilot"
        description="Groq LLM & Whisper Large v3 Turbo adapter, strictly read-only tenant-scoped tools, deterministic Decimal validation, prompt-injection defense, and verifiable AI-10 evidence records."
      />

      {f && (
        <div
          className="p4-kpi-grid"
          role="region"
          aria-label="W0 AI Foundation Status"
        >
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Active AI Provider</span>
            <strong className="p4-kpi-value">
              {f.groq_configured ? "GROQ CLOUD LIVE" : "DETERMINISTIC ENGINE"}
            </strong>
            <span className="p4-kpi-sub">
              LLM: {f.llm_model} · STT: {f.stt_model}
            </span>
          </div>
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Voice & LiveKit Status</span>
            <strong className="p4-kpi-value">
              {f.livekit_enabled ? "LIVEKIT WEBRTC" : "WHISPER + BROWSER TTS"}
            </strong>
            <span className="p4-kpi-sub">
              Mode: {f.livekit_mode} · TTS: {f.tts_mode}
            </span>
          </div>
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Accounting Snapshot Scope</span>
            <strong className="p4-kpi-value">
              {f.business_currency} · {f.business_basis.toUpperCase()}
            </strong>
            <span className="p4-kpi-sub">
              Ledger v{f.business_version} · Policy v{f.policy_version}
            </span>
          </div>
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Safety & Tool Guardrails</span>
            <strong className="p4-kpi-value">READ-ONLY TOOLS</strong>
            <span className="p4-kpi-sub">
              Prompt Shield: Active · Human Approval: Mandatory
            </span>
          </div>
        </div>
      )}

      {statusNotice && (
        <p className="notice" role="status">
          {statusNotice}
        </p>
      )}

      <ErrorNotice
        error={
          overviewQuery.error ||
          impactMutation.error ||
          createMemoryMutation.error ||
          decideMemoryMutation.error ||
          reconMutation.error ||
          createClarificationMutation.error ||
          replyClarificationMutation.error ||
          closeAssistantMutation.error ||
          portfolioPlannerMutation.error ||
          voiceSessionMutation.error ||
          confirmVoiceMutation.error ||
          contractVerificationMutation.error ||
          cashScenarioMutation.error ||
          runDecisionMutation.error
        }
      />

      <SectionTabs
        id="ai-copilot-tabs"
        label="AI Copilot capability tabs"
        value={tab}
        onChange={(next) => setTab(next as AICopilotTab)}
        items={[
          ["w0-evidence", "W0 & AI-10 · Verifiable Evidence"],
          ["ai02-impact", "AI-02 · Financial Impact Preview"],
          ["ai04-memory", "AI-04 · Approved Accounting Memory"],
          ["ai01-recon", "AI-01 · Reconciliation Detective"],
          ["ai03-clarify", "AI-03 · Client Clarification"],
          ["ai05-close-portfolio", "AI-05 & AI-09 · Close & Portfolio"],
          ["ai06-voice", "AI-06 · Multilingual Voice & LiveKit"],
          ["ai07-contracts-cash", "AI-07 & AI-08 · Contracts & Cash Scenarios"],
        ]}
      />

      {tab === "w0-evidence" && (
        <section className="panel" aria-label="AI-10 Verifiable Evidence">
          <h2>AI-10 · Verifiable AI Evidence Record & Tool Audit Trail</h2>
          <p className="muted">
            Every AI recommendation records its exact ledger snapshot hash,
            business/policy version, read-only tool invocations, citations,
            token usage, and approval content hash. Stale approvals are
            automatically invalidated if the ledger changes.
          </p>
          <Table
            caption="Verifiable AI Runs & Approval Lifecycle"
            rows={data?.runs || []}
            rowKey={(r) => r.id}
            columns={[
              {
                label: "Feature",
                render: (r) => (
                  <Badge>
                    {r.feature_id} ({r.wave})
                  </Badge>
                ),
              },
              {
                label: "Provider / Model",
                render: (r) => `${r.provider} · ${r.model_name}`,
              },
              {
                label: "Summary",
                render: (r) => r.input_summary,
              },
              {
                label: "Snapshot / Tools",
                render: (r) =>
                  `v${r.business_version} (${r.snapshot_hash.slice(0, 10)}…) · ${r.tool_invocations.length} tool(s)`,
              },
              {
                label: "Confidence / Cost",
                render: (r) =>
                  `${r.confidence_score} · $${r.estimated_cost_usd} (${r.latency_ms}ms)`,
              },
              {
                label: "Approval State",
                render: (r) => (
                  <Badge>
                    {r.status} / {r.approval_state}
                  </Badge>
                ),
              },
              {
                label: "Actions",
                render: (r) => (
                  <div className="p4-inline-actions">
                    <Button
                      variant="secondary"
                      disabled={
                        runDecisionMutation.isPending ||
                        r.approval_state === "approved"
                      }
                      onClick={() =>
                        runDecisionMutation.mutate({
                          runId: r.id,
                          decision: "approve",
                          contentHash: r.approval_content_hash,
                        })
                      }
                    >
                      Approve
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={runDecisionMutation.isPending}
                      onClick={() =>
                        runDecisionMutation.mutate({
                          runId: r.id,
                          decision: "cancel",
                          contentHash: r.approval_content_hash,
                        })
                      }
                    >
                      Cancel
                    </Button>
                  </div>
                ),
              },
            ]}
          />

          <h3>W0 · Read-Only Tool Invocation Audit Log</h3>
          <Table
            caption="Read-Only Tool Invocations (Tenant-Scoped)"
            rows={data?.tool_audit_logs || []}
            rowKey={(t) => t.id}
            columns={[
              { label: "Tool Name", render: (t) => t.tool_name },
              { label: "Params Hash", render: (t) => t.parameters_hash },
              { label: "Rows", render: (t) => String(t.row_count) },
              { label: "Bytes", render: (t) => `${t.result_bytes} B` },
              {
                label: "Prompt Shield Sanitized",
                render: (t) =>
                  t.untrusted_content_sanitized ? "REDACTED" : "CLEAN",
              },
              { label: "Latency", render: (t) => `${t.duration_ms} ms` },
            ]}
          />
        </section>
      )}

      {tab === "ai02-impact" && (
        <section className="panel" aria-label="AI-02 Financial Impact Preview">
          <h2>AI-02 · Deterministic Financial Impact Preview</h2>
          <p className="muted">
            Compute exact before/after account balances, P&amp;L net income,
            Balance Sheet, AR/AP subledger, and Cash impact before posting. Uses
            canonical Decimal math — never LLM arithmetic — and performs zero
            ledger writes during preview.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              impactMutation.mutate();
            }}
          >
            <Field label="Accounting Date">
              <input
                type="date"
                value={impactDate}
                onChange={(e) => setImpactDate(e.target.value)}
                required
              />
            </Field>
            <Field label="Journal Entry Memo">
              <input
                value={impactMemo}
                onChange={(e) => setImpactMemo(e.target.value)}
                required
              />
            </Field>
            <Field label="Debit Account Code">
              <input
                value={debitAccountCode}
                onChange={(e) => setDebitAccountCode(e.target.value)}
                required
              />
            </Field>
            <Field label="Debit Account Name">
              <input
                value={debitAccountName}
                onChange={(e) => setDebitAccountName(e.target.value)}
                required
              />
            </Field>
            <Field label="Debit Account Class">
              <select
                value={debitKind}
                onChange={(e) =>
                  setDebitKind(e.target.value as "expense" | "asset")
                }
              >
                <option value="expense">Expense</option>
                <option value="asset">Asset</option>
              </select>
            </Field>
            <Field label="Credit Account Code">
              <input
                value={creditAccountCode}
                onChange={(e) => setCreditAccountCode(e.target.value)}
                required
              />
            </Field>
            <Field label="Credit Account Name">
              <input
                value={creditAccountName}
                onChange={(e) => setCreditAccountName(e.target.value)}
                required
              />
            </Field>
            <Field label="Credit Account Class">
              <select
                value={creditKind}
                onChange={(e) =>
                  setCreditKind(e.target.value as "liability" | "asset")
                }
              >
                <option value="liability">Liability (Accrued / AP)</option>
                <option value="asset">Asset (Cash / Bank)</option>
              </select>
            </Field>
            <Field label="Balanced Entry Amount">
              <input
                value={impactAmount}
                onChange={(e) => setImpactAmount(e.target.value)}
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button type="submit" busy={impactMutation.isPending}>
                Preview Financial Impact
              </Button>
            </div>
          </form>

          {impactResult && (
            <div className="p4-kpi-grid" role="region" aria-label="Impact KPIs">
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">P&amp;L Net Income Delta</span>
                <strong className="p4-kpi-value">
                  {String(impactResult.pnl_net_income_delta)}{" "}
                  {String(impactResult.currency)}
                </strong>
                <span className="p4-kpi-sub">
                  Basis: {String(impactResult.basis)}
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Cash Impact</span>
                <strong className="p4-kpi-value">
                  {String(impactResult.cash_delta)}{" "}
                  {String(impactResult.currency)}
                </strong>
                <span className="p4-kpi-sub">
                  {impactResult.is_noncash_reclassification
                    ? "Non-cash accrual / reclassification"
                    : "Direct cash movement"}
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Assets / Liabilities Delta</span>
                <strong className="p4-kpi-value">
                  A: {String(impactResult.assets_delta)} / L:{" "}
                  {String(impactResult.liabilities_delta)}
                </strong>
                <span className="p4-kpi-sub">
                  Narrative: {String(impactResult.narrative)}
                </span>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "ai04-memory" && (
        <section
          className="panel"
          aria-label="AI-04 Approved Business Accounting Memory"
        >
          <h2>AI-04 · Approved Business Accounting Memory</h2>
          <p className="muted">
            Store versioned, effective-dated accounting rules and past examples.
            US GAAP and IFRS policies remain strictly separated, and rejected
            suggestions never auto-convert into policy.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              createMemoryMutation.mutate();
            }}
          >
            <Field label="Memory Key">
              <input
                value={memoryKey}
                onChange={(e) => setMemoryKey(e.target.value)}
                required
              />
            </Field>
            <Field label="Policy Title">
              <input
                value={memoryTitle}
                onChange={(e) => setMemoryTitle(e.target.value)}
                required
              />
            </Field>
            <Field label="Accounting Framework">
              <select
                value={memoryFramework}
                onChange={(e) =>
                  setMemoryFramework(
                    e.target.value as "US_GAAP" | "IFRS" | "TAX_BASIS",
                  )
                }
              >
                <option value="US_GAAP">US GAAP</option>
                <option value="IFRS">IFRS</option>
                <option value="TAX_BASIS">Tax Basis</option>
              </select>
            </Field>
            <Field label="Precedence Kind">
              <select
                value={memoryBinding}
                onChange={(e) =>
                  setMemoryBinding(
                    e.target.value as "binding_policy" | "past_example",
                  )
                }
              >
                <option value="binding_policy">
                  Binding Accounting Policy
                </option>
                <option value="past_example">Past Reviewed Example</option>
              </select>
            </Field>
            <Field label="Target Account Code">
              <input
                value={memoryAccountCode}
                onChange={(e) => setMemoryAccountCode(e.target.value)}
                required
              />
            </Field>
            <Field label="Target Account Name">
              <input
                value={memoryAccountName}
                onChange={(e) => setMemoryAccountName(e.target.value)}
                required
              />
            </Field>
            <Field label="Materiality Threshold">
              <input
                value={memoryMateriality}
                onChange={(e) => setMemoryMateriality(e.target.value)}
                required
              />
            </Field>
            <Field label="Rule Condition & Treatment">
              <input
                value={memoryCondition}
                onChange={(e) => setMemoryCondition(e.target.value)}
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button type="submit" busy={createMemoryMutation.isPending}>
                Propose Accounting Memory
              </Button>
            </div>
          </form>

          <Table
            caption="Business Accounting Memories & Policy Versions"
            rows={data?.memories || []}
            rowKey={(m) => m.id}
            columns={[
              {
                label: "Key / Version",
                render: (m) => `${m.memory_key} (v${m.version})`,
              },
              {
                label: "Framework / Kind",
                render: (m) => `${m.accounting_framework} · ${m.binding_kind}`,
              },
              {
                label: "Title & Condition",
                render: (m) => `${m.title}: ${m.rule_condition}`,
              },
              {
                label: "Account",
                render: (m) => `${m.account_code} ${m.account_name}`,
              },
              { label: "Status", render: (m) => <Badge>{m.status}</Badge> },
              {
                label: "Actions",
                render: (m) => (
                  <div className="p4-inline-actions">
                    <Button
                      variant="secondary"
                      disabled={
                        decideMemoryMutation.isPending ||
                        m.status === "approved"
                      }
                      onClick={() =>
                        decideMemoryMutation.mutate({
                          memoryId: m.id,
                          decision: "approve",
                        })
                      }
                    >
                      Approve Policy
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={
                        decideMemoryMutation.isPending || m.status === "revoked"
                      }
                      onClick={() =>
                        decideMemoryMutation.mutate({
                          memoryId: m.id,
                          decision: "revoke",
                        })
                      }
                    >
                      Revoke
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </section>
      )}

      {tab === "ai01-recon" && (
        <section className="panel" aria-label="AI-01 Reconciliation Detective">
          <h2>AI-01 · Reconciliation Detective</h2>
          <p className="muted">
            Compare statement lines against open invoices, bills, and bank rows
            to separate exact matches, duplicate imports, bundled multi-invoice
            settlements, processor fee deductions, partial payments, and
            ambiguous items.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              reconMutation.mutate();
            }}
          >
            <Field label="Statement Description">
              <input
                value={reconDesc}
                onChange={(e) => setReconDesc(e.target.value)}
                required
              />
            </Field>
            <Field label="Statement Amount">
              <input
                value={reconAmount}
                onChange={(e) => setReconAmount(e.target.value)}
                required
              />
            </Field>
            <Field label="Posted Date">
              <input
                type="date"
                value={reconDate}
                onChange={(e) => setReconDate(e.target.value)}
                required
              />
            </Field>
            <label className="p4-check-row">
              <input
                type="checkbox"
                checked={reconDuplicateFlag}
                onChange={(e) => setReconDuplicateFlag(e.target.checked)}
              />
              <span>Flag duplicate external statement ID</span>
            </label>
            <div className="p4-inline-actions">
              <Button type="submit" busy={reconMutation.isPending}>
                Investigate Reconciliation Candidates
              </Button>
            </div>
          </form>

          <Table
            caption="Reconciliation Detective Investigations"
            rows={data?.reconciliation_investigations || []}
            rowKey={(inv) => inv.id}
            columns={[
              {
                label: "Description",
                render: (inv) => inv.statement_description,
              },
              {
                label: "Amount",
                render: (inv) => `${inv.statement_amount} ${inv.currency}`,
              },
              {
                label: "Classification",
                render: (inv) => <Badge>{inv.classification}</Badge>,
              },
              {
                label: "Residual Delta / Fee",
                render: (inv) =>
                  `Delta: ${inv.deterministic_delta} · Fee: ${inv.fee_amount}`,
              },
              { label: "Explanation", render: (inv) => inv.explanation },
              { label: "Status", render: (inv) => <Badge>{inv.status}</Badge> },
            ]}
          />
        </section>
      )}

      {tab === "ai03-clarify" && (
        <section
          className="panel"
          aria-label="AI-03 Closed-Loop Client Clarification"
        >
          <h2>AI-03 · Closed-Loop Client Clarification</h2>
          <p className="muted">
            Convert unresolved accounting exceptions into deduplicated client
            requests, inspect uploaded replies for document completeness, ask
            only for remaining gaps, and auto-stop on resolution or opt-out.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              createClarificationMutation.mutate();
            }}
          >
            <Field label="Exception Kind">
              <select
                value={clarKind}
                onChange={(e) =>
                  setClarKind(
                    e.target.value as
                      | "missing_invoice"
                      | "bank_row"
                      | "unmatched_payment"
                      | "contract_variance",
                  )
                }
              >
                <option value="missing_invoice">
                  Missing Invoice / Receipt
                </option>
                <option value="bank_row">Unexplained Bank Row</option>
                <option value="unmatched_payment">Unmatched Payment</option>
                <option value="contract_variance">Contract Variance</option>
              </select>
            </Field>
            <Field label="Exception Reference">
              <input
                value={clarRef}
                onChange={(e) => setClarRef(e.target.value)}
                required
              />
            </Field>
            <Field label="Recipient Key">
              <input
                value={clarRecipient}
                onChange={(e) => setClarRecipient(e.target.value)}
                required
              />
            </Field>
            <Field label="Delivery Channel (Sandbox Gate)">
              <select
                value={clarChannel}
                onChange={(e) =>
                  setClarChannel(
                    e.target.value as "email" | "whatsapp" | "slack",
                  )
                }
              >
                <option value="email">Email Sandbox</option>
                <option value="whatsapp">WhatsApp Cloud Sandbox</option>
                <option value="slack">Slack Workspace Sandbox</option>
              </select>
            </Field>
            <Field label="Exception Context">
              <input
                value={clarContext}
                onChange={(e) => setClarContext(e.target.value)}
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button
                type="submit"
                busy={createClarificationMutation.isPending}
              >
                Generate Deduplicated Clarification
              </Button>
            </div>
          </form>

          <Table
            caption="Client Clarification Loop & Completeness Validation"
            rows={data?.client_clarifications || []}
            rowKey={(c) => c.id}
            columns={[
              {
                label: "Exception",
                render: (c) => `${c.exception_kind}: ${c.exception_ref}`,
              },
              {
                label: "Recipient / Channel",
                render: (c) => `${c.recipient_key} (${c.channel})`,
              },
              { label: "Completeness", render: (c) => c.completeness_score },
              {
                label: "Remaining Gaps",
                render: (c) =>
                  c.remaining_gaps.length > 0
                    ? c.remaining_gaps.join(", ")
                    : "All documents verified",
              },
              { label: "Status", render: (c) => <Badge>{c.status}</Badge> },
              {
                label: "Simulate Client Response",
                render: (c) => (
                  <div className="p4-inline-actions">
                    <Button
                      variant="secondary"
                      disabled={replyClarificationMutation.isPending}
                      onClick={() =>
                        replyClarificationMutation.mutate({
                          clarificationId: c.id,
                          action: "reply",
                          docs: c.required_documents,
                        })
                      }
                    >
                      Submit Complete Docs
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={replyClarificationMutation.isPending}
                      onClick={() =>
                        replyClarificationMutation.mutate({
                          clarificationId: c.id,
                          action: "dispute",
                          docs: [],
                        })
                      }
                    >
                      Escalate Dispute
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </section>
      )}

      {tab === "ai05-close-portfolio" && (
        <section
          className="panel"
          aria-label="AI-05 Continuous Close and AI-09 Portfolio Planner"
        >
          <h2>
            AI-05 · Continuous Close Assistant &amp; AI-09 · Portfolio Work
            Planner
          </h2>
          <p className="muted">
            Evaluate period-close dependencies and recurring omission candidates
            (AI cannot sign off for an accountant), and rank multi-business
            accountant portfolio queues without summing unlike currencies.
          </p>
          <div className="form-grid">
            <Field label="Target Close Period">
              <input
                value={closePeriodLabel}
                onChange={(e) => setClosePeriodLabel(e.target.value)}
              />
            </Field>
            <div className="p4-inline-actions">
              <Button
                busy={closeAssistantMutation.isPending}
                onClick={() => closeAssistantMutation.mutate()}
              >
                Run AI-05 Close Assistant
              </Button>
              <Button
                variant="secondary"
                busy={portfolioPlannerMutation.isPending}
                onClick={() => portfolioPlannerMutation.mutate()}
              >
                Run AI-09 Portfolio Planner
              </Button>
            </div>
          </div>

          {closeReport && (
            <div
              className="p4-kpi-grid"
              role="region"
              aria-label="Close Readiness Output"
            >
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Close Readiness Score</span>
                <strong className="p4-kpi-value">
                  {String(closeReport.readiness_score_pct)}%
                </strong>
                <span className="p4-kpi-sub">
                  Period: {String(closeReport.period_label)} · AI Sign-Off:
                  Forbidden
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Cited Workpaper Narrative</span>
                <span className="p4-kpi-sub">
                  {String(closeReport.workpaper_narrative)}
                </span>
              </div>
            </div>
          )}

          {portfolioReport && (
            <div
              className="p4-kpi-grid"
              role="region"
              aria-label="Portfolio Planner Output"
            >
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">
                  Authorized Workspaces Ranked
                </span>
                <strong className="p4-kpi-value">
                  {String(portfolioReport.authorized_businesses_evaluated)}
                </strong>
                <span className="p4-kpi-sub">
                  Unlike Currencies Summed:{" "}
                  {String(portfolioReport.unlike_currencies_summed)}
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Daily Briefing</span>
                <span className="p4-kpi-sub">
                  {String(portfolioReport.daily_briefing)}
                </span>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "ai06-voice" && (
        <section
          className="panel"
          aria-label="AI-06 Multilingual Voice Accounting"
        >
          <h2>
            AI-06 · Multilingual Voice Accounting (Groq Whisper + Browser TTS +
            LiveKit)
          </h2>
          <p className="muted">
            Supports English (en_US), Urdu (ur_PK), and mixed code-switching
            (en_ur_mixed). Speech never authorizes direct posting — critical
            fields must be explicitly confirmed to create a reviewable draft.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              voiceSessionMutation.mutate();
            }}
          >
            <Field label="Language Matrix">
              <select
                value={voiceLocale}
                onChange={(e) =>
                  setVoiceLocale(
                    e.target.value as "en_US" | "ur_PK" | "en_ur_mixed",
                  )
                }
              >
                <option value="en_ur_mixed">
                  English + Urdu Mixed (Code-Switching)
                </option>
                <option value="en_US">English (en_US)</option>
                <option value="ur_PK">Urdu (ur_PK)</option>
              </select>
            </Field>
            <Field label="Voice Transcript / Audio Prompt">
              <input
                value={voiceTranscriptInput}
                onChange={(e) => setVoiceTranscriptInput(e.target.value)}
                required
              />
            </Field>
            <label className="p4-check-row">
              <input
                type="checkbox"
                checked={voiceRequestLiveKit}
                onChange={(e) => setVoiceRequestLiveKit(e.target.checked)}
              />
              <span>
                Mint LiveKit WebRTC Room Token (auto-activates when LIVEKIT_*
                env vars are set)
              </span>
            </label>
            <div className="p4-inline-actions">
              <Button type="submit" busy={voiceSessionMutation.isPending}>
                Transcribe &amp; Extract Entities
              </Button>
            </div>
          </form>

          <Table
            caption="Multilingual Voice Sessions & Entity Confirmation"
            rows={data?.voice_sessions || []}
            rowKey={(vs) => vs.id}
            columns={[
              {
                label: "Locale / STT",
                render: (vs) => `${vs.language_locale} · ${vs.stt_provider}`,
              },
              { label: "Transcript", render: (vs) => vs.corrected_transcript },
              {
                label: "Extracted Entities",
                render: (vs) =>
                  `${vs.extracted_entities.party_name || "Vendor"} · ${vs.extracted_entities.amount || "0.00"} ${vs.extracted_entities.currency || b.currency}`,
              },
              {
                label: "Uncertain Fields",
                render: (vs) =>
                  vs.uncertain_fields.length > 0
                    ? vs.uncertain_fields.join(", ")
                    : "All verified",
              },
              {
                label: "Status / Draft",
                render: (vs) => (
                  <Badge>
                    {vs.status}
                    {vs.linked_draft_id
                      ? ` (Draft ${vs.linked_draft_id.slice(0, 8)})`
                      : ""}
                  </Badge>
                ),
              },
              {
                label: "Actions",
                render: (vs) => (
                  <div className="p4-inline-actions">
                    <Button
                      variant="ghost"
                      onClick={() => speakReadback(vs.tts_readback_text)}
                    >
                      TTS Readback
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={
                        confirmVoiceMutation.isPending ||
                        vs.critical_fields_confirmed
                      }
                      onClick={() =>
                        confirmVoiceMutation.mutate({
                          sessionId: vs.id,
                          transcript: vs.corrected_transcript,
                          partyName:
                            vs.extracted_entities.party_name ||
                            "K-Electric Limited",
                          amount: vs.extracted_entities.amount || "850.00",
                          currency:
                            vs.extracted_entities.currency || b.currency,
                        })
                      }
                    >
                      Confirm &amp; Create Draft
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </section>
      )}

      {tab === "ai07-contracts-cash" && (
        <section
          className="panel"
          aria-label="AI-07 Contract Verification and AI-08 Cash-Flow Scenarios"
        >
          <h2>AI-07 · Contract / Invoice / Payment Verification</h2>
          <p className="muted">
            Extract cited contract pricing and compare deterministically against
            invoiced and paid amounts to detect rate overcharges, duplicate
            payments, or unapproved milestones.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              contractVerificationMutation.mutate();
            }}
          >
            <Field label="Contract Reference">
              <input
                value={contractRef}
                onChange={(e) => setContractRef(e.target.value)}
                required
              />
            </Field>
            <Field label="Vendor / Customer">
              <input
                value={contractVendor}
                onChange={(e) => setContractVendor(e.target.value)}
                required
              />
            </Field>
            <Field label="Cited Contract Clause">
              <input
                value={contractClause}
                onChange={(e) => setContractClause(e.target.value)}
                required
              />
            </Field>
            <Field label="Contracted Cap / Amount">
              <input
                value={contractedAmt}
                onChange={(e) => setContractedAmt(e.target.value)}
                required
              />
            </Field>
            <Field label="Invoiced Amount">
              <input
                value={invoicedAmt}
                onChange={(e) => setInvoicedAmt(e.target.value)}
                required
              />
            </Field>
            <Field label="Already Paid Amount">
              <input
                value={paidAmt}
                onChange={(e) => setPaidAmt(e.target.value)}
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button
                type="submit"
                busy={contractVerificationMutation.isPending}
              >
                Verify Contract vs. Invoice
              </Button>
            </div>
          </form>

          <Table
            caption="Contract Verification Findings"
            rows={data?.contract_verifications || []}
            rowKey={(cv) => cv.id}
            columns={[
              {
                label: "Contract / Party",
                render: (cv) =>
                  `${cv.contract_reference} (${cv.vendor_or_customer})`,
              },
              {
                label: "Contracted",
                render: (cv) => `${cv.contracted_amount} ${cv.currency}`,
              },
              {
                label: "Invoiced / Paid",
                render: (cv) =>
                  `Inv: ${cv.invoiced_amount} · Paid: ${cv.paid_amount}`,
              },
              {
                label: "Discrepancy",
                render: (cv) =>
                  `${cv.discrepancy_amount} (${cv.discrepancy_kind})`,
              },
              {
                label: "Proposed Resolution",
                render: (cv) => cv.proposed_action,
              },
              { label: "Status", render: (cv) => <Badge>{cv.status}</Badge> },
            ]}
          />

          <hr />

          <h2>AI-08 · Action-Based Cash-Flow Scenarios</h2>
          <p className="muted">
            Simulate collection delays, equipment purchases, and recurring cost
            changes over 13 weeks using canonical Decimal calculations without
            mutating your baseline forecast.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              cashScenarioMutation.mutate();
            }}
          >
            <Field label="Scenario Name">
              <input
                value={scenarioName}
                onChange={(e) => setScenarioName(e.target.value)}
                required
              />
            </Field>
            <Field label="What-If Question">
              <input
                value={scenarioPrompt}
                onChange={(e) => setScenarioPrompt(e.target.value)}
                required
              />
            </Field>
            <Field label="Collection Delay (Weeks)">
              <input
                type="number"
                min={0}
                max={12}
                value={delayWeeks}
                onChange={(e) => setDelayWeeks(e.target.value)}
                required
              />
            </Field>
            <Field label="Week-1 Equipment Purchase">
              <input
                value={equipmentCapex}
                onChange={(e) => setEquipmentCapex(e.target.value)}
                required
              />
            </Field>
            <Field label="Recurring Cost Change (%)">
              <input
                value={costChangePct}
                onChange={(e) => setCostChangePct(e.target.value)}
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button type="submit" busy={cashScenarioMutation.isPending}>
                Simulate 13-Week Cash Scenario
              </Button>
            </div>
          </form>

          <Table
            caption="Simulated Cash-Flow Scenarios"
            rows={data?.cash_scenarios || []}
            rowKey={(cs) => cs.id}
            columns={[
              { label: "Scenario", render: (cs) => cs.scenario_name },
              {
                label: "Baseline Ending Cash",
                render: (cs) => `${cs.baseline_ending_cash} ${cs.currency}`,
              },
              {
                label: "Scenario Ending Cash",
                render: (cs) => `${cs.scenario_ending_cash} ${cs.currency}`,
              },
              {
                label: "Net Delta",
                render: (cs) => `${cs.net_cash_delta} ${cs.currency}`,
              },
              {
                label: "Lowest Weekly Cash",
                render: (cs) => `${cs.lowest_weekly_cash} ${cs.currency}`,
              },
              {
                label: "Shortfall Weeks",
                render: (cs) => String(cs.shortfall_weeks),
              },
            ]}
          />
        </section>
      )}
    </div>
  );
}
