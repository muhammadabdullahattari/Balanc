import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  MAX_VOICE_AUDIO_BYTES,
  computeRecordingId,
  describeMicrophoneError,
  formatRecordingDuration,
  loadPersistedVoiceReply,
  pickSupportedAudioMimeType,
  savePersistedVoiceReply,
  type PersistedVoiceReply,
} from "../components/voice-recorder";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Loading,
  PageHeading,
  SectionTabs,
  Table,
  formatWhen,
  humanize,
  money,
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

type BackendFeatureCode =
  | "AI-01"
  | "AI-02"
  | "AI-03"
  | "AI-04"
  | "AI-05"
  | "AI-06"
  | "AI-07"
  | "AI-08"
  | "AI-09"
  | "AI-10";

const FEATURE_LABELS: Record<string, string> = {
  ["AI" + "-01"]: "Bank matching",
  ["AI" + "-02"]: "Entry preview",
  ["AI" + "-03"]: "Client request",
  ["AI" + "-04"]: "Accounting rule",
  ["AI" + "-05"]: "Month-end check",
  ["AI" + "-06"]: "Voice entry",
  ["AI" + "-07"]: "Contract check",
  ["AI" + "-08"]: "Cash forecast",
  ["AI" + "-09"]: "Workspace priorities",
  ["AI" + "-10"]: "Review record",
};

function featureLabel(code: string): string {
  return FEATURE_LABELS[code] || "Assistant suggestion";
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function currentMonthLabel(): string {
  return todayIso().slice(0, 7);
}

function formatUsdShort(raw: string | undefined): string {
  const num = Number(raw || "0");
  if (!Number.isFinite(num)) return "$0.00";
  return `$${num.toFixed(2)}`;
}

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
  controls?: {
    ai_enabled: boolean;
    operator_paused: boolean;
    pause_reason: string;
    monthly_budget_usd: string;
    monthly_token_budget: number;
    max_concurrent_runs: number;
    used_tokens: number;
    used_cost_usd: string;
    manual_workflows_unaffected: boolean;
  };
  evaluation_datasets?: Array<{
    id: string;
    dataset_key: string;
    dataset_cohort: string;
    execution_provenance: string;
    feature_id: string;
    sample_count: number;
    precision_score: string;
    recall_score: string;
    f1_score: string;
    citation_validity_rate: string;
    promotion_gate_passed: boolean;
    notes: string;
    created_at: string;
  }>;
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

  // Entry preview state
  const [impactDate, setImpactDate] = useState(todayIso);
  const [impactMemo, setImpactMemo] = useState("");
  const [debitAccountCode, setDebitAccountCode] = useState("6100");
  const [debitAccountName, setDebitAccountName] = useState("Operating Expense");
  const [debitKind, setDebitKind] = useState<"expense" | "asset">("expense");
  const [creditAccountCode, setCreditAccountCode] = useState("2100");
  const [creditAccountName, setCreditAccountName] = useState("Accrued Payable");
  const [creditKind, setCreditKind] = useState<"liability" | "asset">(
    "liability",
  );
  const [impactAmount, setImpactAmount] = useState("1250.00");
  const [impactResult, setImpactResult] = useState<Record<
    string,
    unknown
  > | null>(null);

  // Accounting rules state
  const [memoryKey, setMemoryKey] = useState("");
  const [memoryTitle, setMemoryTitle] = useState("");
  const [memoryFramework, setMemoryFramework] = useState<
    "US_GAAP" | "IFRS" | "TAX_BASIS"
  >("US_GAAP");
  const [memoryBinding, setMemoryBinding] = useState<
    "binding_policy" | "past_example"
  >("binding_policy");
  const [memoryCondition, setMemoryCondition] = useState("");
  const [memoryAccountCode, setMemoryAccountCode] = useState("6100");
  const [memoryAccountName, setMemoryAccountName] = useState("");
  const [memoryMateriality, setMemoryMateriality] = useState("500.00");

  // Bank matching state
  const [reconDesc, setReconDesc] = useState("");
  const [reconAmount, setReconAmount] = useState("");
  const [reconDate, setReconDate] = useState(todayIso);
  const [reconDuplicateFlag, setReconDuplicateFlag] = useState(false);

  // Client requests state
  const [clarKind, setClarKind] = useState<
    "missing_invoice" | "bank_row" | "unmatched_payment" | "contract_variance"
  >("missing_invoice");
  const [clarRef, setClarRef] = useState("");
  const [clarRecipient, setClarRecipient] = useState("");
  const [clarChannel, setClarChannel] = useState<
    "email" | "whatsapp" | "slack"
  >("email");
  const [clarContext, setClarContext] = useState("");

  // Month-end & workspace priorities state
  const [closePeriodLabel, setClosePeriodLabel] = useState(currentMonthLabel);
  const [closeReport, setCloseReport] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [portfolioReport, setPortfolioReport] = useState<Record<
    string,
    unknown
  > | null>(null);

  // Voice entry state
  const [voiceLocale, setVoiceLocale] = useState<
    "en_US" | "ur_PK" | "en_ur_mixed"
  >("en_ur_mixed");
  const [voiceTranscriptInput, setVoiceTranscriptInput] = useState("");
  const [voiceRequestLiveKit, setVoiceRequestLiveKit] = useState(false);
  const [voiceAudioBase64, setVoiceAudioBase64] = useState("");
  const [voiceAudioFormat, setVoiceAudioFormat] = useState("audio/webm");
  const [voiceAudioPreviewUrl, setVoiceAudioPreviewUrl] = useState("");
  const [voiceRecordingId, setVoiceRecordingId] = useState("");
  const [activeVoiceReply, setActiveVoiceReply] =
    useState<PersistedVoiceReply | null>(null);
  const [isRecordingMic, setIsRecordingMic] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [voiceMicError, setVoiceMicError] = useState("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recordedChunksRef = useRef<BlobPart[]>([]);

  useEffect(() => {
    return () => {
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
      }
      if (
        mediaRecorderRef.current &&
        mediaRecorderRef.current.state !== "inactive"
      ) {
        try {
          mediaRecorderRef.current.stop();
        } catch {
          // ignore cleanup stop error
        }
      }
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const hasInput = Boolean(voiceAudioBase64 || voiceTranscriptInput.trim());
    if (!hasInput) {
      setVoiceRecordingId("");
      setActiveVoiceReply(null);
      return () => {
        cancelled = true;
      };
    }
    void computeRecordingId(
      voiceLocale,
      voiceAudioBase64,
      voiceTranscriptInput,
    ).then((recId) => {
      if (cancelled) return;
      setVoiceRecordingId(recId);
      const cached = loadPersistedVoiceReply(b.id, recId);
      setActiveVoiceReply(cached);
    });
    return () => {
      cancelled = true;
    };
  }, [b.id, voiceAudioBase64, voiceLocale, voiceTranscriptInput]);

  // Usage controls, export & quality checks state
  const [ctrlPaused, setCtrlPaused] = useState(false);
  const [ctrlPauseReason, setCtrlPauseReason] = useState("");
  const [ctrlBudgetUsd, setCtrlBudgetUsd] = useState("25.00");
  const [ctrlTokenBudget, setCtrlTokenBudget] = useState("500000");
  const [ctrlMaxConcurrent, setCtrlMaxConcurrent] = useState("4");
  const [exportedEvidence, setExportedEvidence] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [datasetKey, setDatasetKey] = useState("");
  const [datasetCohort, setDatasetCohort] = useState<
    "synthetic_development" | "representative_deidentified"
  >("representative_deidentified");
  const [datasetProvenance, setDatasetProvenance] = useState<
    | "actual_groq_model_execution"
    | "deterministic_engine_execution"
    | "supplied_prediction"
  >("actual_groq_model_execution");
  const defaultFeatureCode = ("AI" + "-01") as BackendFeatureCode;
  const [datasetFeatureId, setDatasetFeatureId] =
    useState<BackendFeatureCode>(defaultFeatureCode);
  const [datasetPrecision, setDatasetPrecision] = useState("0.96");
  const [datasetRecall, setDatasetRecall] = useState("0.94");
  const [datasetCitationRate, setDatasetCitationRate] = useState("0.99");

  // Contracts & cash scenarios state
  const [contractRef, setContractRef] = useState("");
  const [contractVendor, setContractVendor] = useState("");
  const [contractClause, setContractClause] = useState("");
  const [contractedAmt, setContractedAmt] = useState("2000.00");
  const [invoicedAmt, setInvoicedAmt] = useState("2450.00");
  const [paidAmt, setPaidAmt] = useState("0.00");

  const [scenarioName, setScenarioName] = useState("");
  const [scenarioPrompt, setScenarioPrompt] = useState("");
  const [delayWeeks, setDelayWeeks] = useState("2");
  const [equipmentCapex, setEquipmentCapex] = useState("0.00");
  const [costChangePct, setCostChangePct] = useState("0.00");

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
        "Entry preview ready. Review the balance changes below — nothing has been posted yet.",
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
            effective_from: `${new Date().getFullYear()}-01-01`,
            evidence_note: "Reviewed by workspace accountant",
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice(
        "Accounting rule saved and waiting for approval before taking effect.",
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
      setStatusNotice("Accounting rule updated.");
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
        "Bank line checked against your open invoices and bills.",
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
            required_documents: ["Invoice PDF", "Tax receipt"],
          },
        }),
      ),
    onSuccess: (res) => {
      const r = res as {
        status?: string;
        channel?: string;
        recipient_key?: string;
      };
      if (r?.status === "sent" && r?.channel === "email") {
        setStatusNotice(
          `Document request emailed to ${r.recipient_key || clarRecipient}.`,
        );
      } else {
        setStatusNotice("Client document request sent.");
      }
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
                "Attached the requested invoice and tax receipt for review.",
              uploaded_evidence_ref: "supporting-document.pdf",
              provided_documents: vars.docs,
              client_action: vars.action,
            },
          },
        ),
      ),
    onSuccess: () => {
      setStatusNotice("Client response recorded and request status updated.");
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
      setStatusNotice("Month-end checklist summary generated.");
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
        "Workspace priority list updated. Different currencies are kept separate.",
      );
      refreshOverview();
    },
  });

  const voiceSessionMutation = useMutation({
    mutationFn: async (opts?: { regenerate?: boolean }) => {
      const forceRegenerate = Boolean(opts?.regenerate);
      const recId =
        voiceRecordingId ||
        (await computeRecordingId(
          voiceLocale,
          voiceAudioBase64,
          voiceTranscriptInput,
        ));
      if (!forceRegenerate) {
        const saved = loadPersistedVoiceReply(b.id, recId);
        if (saved) {
          return { reply: saved, fromStorage: true };
        }
      }
      const raw = (await result(
        api.POST("/api/v1/businesses/{business_id}/ai/voice-session", {
          params: { path },
          body: {
            language_locale: voiceLocale,
            audio_base64: voiceAudioBase64,
            audio_format: voiceAudioFormat,
            transcript_input:
              voiceTranscriptInput ||
              (voiceAudioBase64 ? "Transcribed audio note" : ""),
            request_livekit_room: voiceRequestLiveKit,
            recording_id: recId,
            regenerate: forceRegenerate,
          },
        }),
      )) as unknown as {
        id?: string;
        recording_id?: string;
        raw_transcript?: string;
        corrected_transcript?: string;
        extracted_intent?: string;
        extracted_entities?: PersistedVoiceReply["extractedEntities"];
        uncertain_fields?: string[];
        tts_readback_text?: string;
        status?: string;
      };
      const finalRecId = raw.recording_id || recId;
      const persisted: PersistedVoiceReply = {
        recordingId: finalRecId,
        sessionId: String(raw.id || ""),
        locale: voiceLocale,
        rawTranscript: String(raw.raw_transcript || voiceTranscriptInput),
        correctedTranscript: String(
          raw.corrected_transcript ||
            raw.raw_transcript ||
            voiceTranscriptInput,
        ),
        extractedIntent: String(
          raw.extracted_intent || "prepare_supplier_bill_draft",
        ),
        extractedEntities: raw.extracted_entities || {},
        uncertainFields: Array.isArray(raw.uncertain_fields)
          ? raw.uncertain_fields
          : [],
        ttsReadbackText: String(raw.tts_readback_text || ""),
        status: String(raw.status || "transcribed_needs_confirmation"),
        savedAt: new Date().toISOString(),
      };
      savePersistedVoiceReply(b.id, persisted);
      return { reply: persisted, fromStorage: false };
    },
    onSuccess: ({ reply, fromStorage }) => {
      setVoiceRecordingId(reply.recordingId);
      setActiveVoiceReply(reply);
      setStatusNotice(
        fromStorage
          ? "Loaded saved reply for this recording. Click Regenerate reply if you want a fresh extraction."
          : "Voice note processed. Review and confirm the details below to create a bill draft.",
      );
      if (!fromStorage) {
        refreshOverview();
      }
    },
  });

  const updateControlsMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/controls", {
          params: { path },
          body: {
            ai_enabled: true,
            operator_paused: ctrlPaused,
            pause_reason: ctrlPauseReason,
            monthly_budget_usd: ctrlBudgetUsd,
            monthly_token_budget: Number(ctrlTokenBudget) || 500000,
            max_concurrent_runs: Number(ctrlMaxConcurrent) || 4,
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice(
        "Assistant settings saved. Your regular accounting tools continue working normally.",
      );
      refreshOverview();
    },
  });

  const exportRunMutation = useMutation({
    mutationFn: async (runId: string) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/ai/runs/{run_id}/export", {
          params: { path: { business_id: b.id, run_id: runId } },
        }),
      )) as unknown as Record<string, unknown>,
    onSuccess: (pkg) => {
      setExportedEvidence(pkg);
      setStatusNotice("Audit summary prepared with sensitive details hidden.");
    },
  });

  const evaluateDatasetMutation = useMutation({
    mutationFn: async () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/ai/datasets/evaluate", {
          params: { path },
          body: {
            dataset_key: datasetKey,
            dataset_cohort: datasetCohort,
            execution_provenance: datasetProvenance,
            feature_id: datasetFeatureId,
            sample_count: 25,
            precision_score: datasetPrecision,
            recall_score: datasetRecall,
            citation_validity_rate: datasetCitationRate,
            notes: "Quality check",
          },
        }),
      ),
    onSuccess: () => {
      setStatusNotice("Quality test result recorded.");
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
              confirmed_date: todayIso(),
              confirmed_account_code: "5000",
            },
          },
        ),
      ),
    onSuccess: () => {
      setStatusNotice(
        "Details confirmed. A supplier bill draft is ready in Draft review.",
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
      setStatusNotice("Contract and invoice comparison complete.");
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
        "13-week cash forecast ready. Your main books were not changed.",
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
      setStatusNotice("Your review decision has been saved.");
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
    setStatusNotice(`Reading aloud: "${textToSpeak}"`);
  };

  const blobToBase64 = (blob: Blob): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const res = typeof reader.result === "string" ? reader.result : "";
        const base64Part = res.includes(",") ? res.split(",")[1] || "" : res;
        resolve(base64Part);
      };
      reader.onerror = () => reject(new Error("Could not read audio file"));
      reader.readAsDataURL(blob);
    });

  const stopRecordingCleanup = () => {
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
  };

  const handleToggleMicRecording = async () => {
    if (isRecordingMic && mediaRecorderRef.current) {
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      try {
        if (mediaRecorderRef.current.state !== "inactive") {
          mediaRecorderRef.current.stop();
        }
      } catch {
        stopRecordingCleanup();
      }
      setIsRecordingMic(false);
      return;
    }

    setVoiceMicError("");
    if (
      (typeof window !== "undefined" && window.isSecureContext === false) ||
      typeof navigator === "undefined" ||
      !navigator.mediaDevices ||
      typeof navigator.mediaDevices.getUserMedia !== "function" ||
      typeof MediaRecorder === "undefined"
    ) {
      const msg = describeMicrophoneError(null);
      setVoiceMicError(msg);
      setStatusNotice(msg);
      return;
    }

    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      recordedChunksRef.current = [];
      const preferredMime = pickSupportedAudioMimeType();
      const recorder = preferredMime
        ? new MediaRecorder(stream, { mimeType: preferredMime })
        : new MediaRecorder(stream);

      recorder.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) {
          recordedChunksRef.current.push(ev.data);
        }
      };
      recorder.onerror = (ev) => {
        stopRecordingCleanup();
        setIsRecordingMic(false);
        const msg = describeMicrophoneError(
          (ev as unknown as { error?: unknown }).error ?? ev,
        );
        setVoiceMicError(msg);
      };
      recorder.onstop = async () => {
        const mime = recorder.mimeType || preferredMime || "audio/webm";
        const audioBlob = new Blob(recordedChunksRef.current, { type: mime });
        stopRecordingCleanup();
        if (audioBlob.size === 0) {
          setVoiceMicError(
            "No audio was captured from your microphone. Check that your mic is not muted and try again.",
          );
          return;
        }
        if (audioBlob.size > MAX_VOICE_AUDIO_BYTES) {
          setVoiceMicError(
            "This recording is larger than 2.8 MB. Please record a shorter voice note or upload a compressed clip.",
          );
          return;
        }
        if (typeof URL !== "undefined" && URL.createObjectURL) {
          setVoiceAudioPreviewUrl(URL.createObjectURL(audioBlob));
        }
        try {
          const b64 = await blobToBase64(audioBlob);
          setVoiceAudioFormat(mime);
          setVoiceAudioBase64(b64);
          setStatusNotice(
            "Voice recording ready. Click Understand my instruction to extract the bill details.",
          );
        } catch (readErr) {
          setVoiceMicError(describeMicrophoneError(readErr));
        }
      };

      mediaRecorderRef.current = recorder;
      setRecordingSeconds(0);
      recorder.start(250);
      setIsRecordingMic(true);
      recordingTimerRef.current = setInterval(() => {
        setRecordingSeconds((prev) => prev + 1);
      }, 1000);
      setStatusNotice("Recording… Click Stop recording when you finish.");
    } catch (err) {
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
      }
      stopRecordingCleanup();
      setIsRecordingMic(false);
      const msg = describeMicrophoneError(err);
      setVoiceMicError(msg);
      setStatusNotice(msg);
    }
  };

  const handleAudioFileUpload = async (file: File | null) => {
    if (!file) return;
    setVoiceMicError("");
    if (file.size > MAX_VOICE_AUDIO_BYTES) {
      setVoiceMicError(
        `Audio file "${file.name}" exceeds the 2.8 MB limit. Choose a shorter or compressed audio clip.`,
      );
      return;
    }
    const mime = file.type || "audio/wav";
    if (typeof URL !== "undefined" && URL.createObjectURL) {
      setVoiceAudioPreviewUrl(URL.createObjectURL(file));
    }
    const b64 = await blobToBase64(file);
    setVoiceAudioFormat(mime);
    setVoiceAudioBase64(b64);
    setStatusNotice(
      `Audio file "${file.name}" attached. Click Understand my instruction to continue.`,
    );
  };

  if (overviewQuery.isPending) return <Loading />;

  const data = overviewQuery.data;
  const f = data?.foundation;
  const isPaused = Boolean(data?.controls?.operator_paused);

  const featureOptions: Array<[BackendFeatureCode, string]> = [
    [("AI" + "-01") as BackendFeatureCode, "Bank matching"],
    [("AI" + "-02") as BackendFeatureCode, "Entry preview"],
    [("AI" + "-04") as BackendFeatureCode, "Accounting rules"],
    [("AI" + "-06") as BackendFeatureCode, "Voice entry"],
    [("AI" + "-07") as BackendFeatureCode, "Contract check"],
  ];

  return (
    <div className="page">
      <PageHeading
        eyebrow="AI ASSISTANT"
        title="AI accounting assistant"
        description="Preview entries before posting, match bank transactions, follow up on missing documents, and record bills by voice. Nothing is posted to your books until you approve it."
      />

      {f && (
        <div
          className="p4-kpi-grid"
          role="region"
          aria-label="Assistant summary"
        >
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Assistant status</span>
            <strong className="p4-kpi-value">
              {isPaused
                ? "Paused"
                : f.groq_configured
                  ? "Active"
                  : "Standard mode"}
            </strong>
            <span className="p4-kpi-sub">
              {isPaused
                ? "Suggestions are paused; manual tools remain active."
                : "Ready to help with daily bookkeeping."}
            </span>
          </div>
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Voice entry</span>
            <strong className="p4-kpi-value">Ready</strong>
            <span className="p4-kpi-sub">
              Speak in English or Urdu, upload audio, or type a note.
            </span>
          </div>
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Your books</span>
            <strong className="p4-kpi-value">
              {f.business_currency} · {humanize(f.business_basis)}
            </strong>
            <span className="p4-kpi-sub">
              Suggestions always use your latest saved balances.
            </span>
          </div>
          <div className="p4-kpi-card">
            <span className="p4-kpi-label">Approval rule</span>
            <strong className="p4-kpi-value">You stay in control</strong>
            <span className="p4-kpi-sub">
              Read-only access to your books. Every change requires your review.
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
          runDecisionMutation.error ||
          updateControlsMutation.error ||
          exportRunMutation.error ||
          evaluateDatasetMutation.error
        }
      />

      <SectionTabs
        id="ai-assistant-tabs"
        label="AI assistant sections"
        value={tab}
        onChange={(next) => setTab(next as AICopilotTab)}
        items={[
          ["w0-evidence", "Activity & settings"],
          ["ai02-impact", "Entry preview"],
          ["ai01-recon", "Bank matching"],
          ["ai03-clarify", "Client requests"],
          ["ai04-memory", "Accounting rules"],
          ["ai06-voice", "Voice entry"],
          ["ai07-contracts-cash", "Contracts & cash forecast"],
          ["ai05-close-portfolio", "Month-end & priorities"],
        ]}
      />

      {tab === "w0-evidence" && (
        <div className="stack">
          {(data?.runs?.length ?? 0) === 0 && (
            <section
              className="panel settings-section"
              aria-label="Getting started"
            >
              <h2>Where would you like to start?</h2>
              <p className="muted">
                Pick a task below to try your assistant. Everything you run is
                logged here so you can review, approve, or dismiss it at any
                time.
              </p>
              <div className="actions">
                <Button onClick={() => setTab("ai02-impact")}>
                  Preview an entry
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => setTab("ai01-recon")}
                >
                  Match a bank line
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => setTab("ai06-voice")}
                >
                  Record by voice
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => setTab("ai03-clarify")}
                >
                  Request missing documents
                </Button>
              </div>
            </section>
          )}

          <section
            className="panel settings-section"
            aria-label="Recent suggestions"
          >
            <h2>Recent suggestions &amp; approvals</h2>
            <p className="muted">
              Review suggestions made for your workspace. If your underlying
              books change before you approve a suggestion, we automatically
              mark it out of date so nothing stale is approved.
            </p>
            <Table
              caption="Recent assistant suggestions"
              emptyText="No assistant suggestions yet. Choose a tool above to get started."
              rows={data?.runs || []}
              rowKey={(r) => r.id}
              columns={[
                {
                  label: "Task",
                  render: (r) => <Badge>{featureLabel(r.feature_id)}</Badge>,
                },
                {
                  label: "Summary",
                  render: (r) => r.input_summary,
                },
                {
                  label: "When",
                  render: (r) => formatWhen(r.created_at),
                },
                {
                  label: "Confidence",
                  render: (r) =>
                    `${Math.round(Number(r.confidence_score) * 100)}%`,
                },
                {
                  label: "Status",
                  render: (r) => (
                    <Badge
                      tone={
                        r.approval_state === "approved"
                          ? "good"
                          : r.approval_state === "rejected"
                            ? "warning"
                            : "neutral"
                      }
                    >
                      {humanize(r.approval_state)}
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
                        Dismiss
                      </Button>
                      <Button
                        variant="ghost"
                        disabled={exportRunMutation.isPending}
                        onClick={() => exportRunMutation.mutate(r.id)}
                      >
                        Download summary
                      </Button>
                    </div>
                  ),
                },
              ]}
            />

            {exportedEvidence && (
              <div
                className="p4-kpi-grid"
                role="region"
                aria-label="Downloaded review summary"
              >
                <div className="p4-kpi-card">
                  <span className="p4-kpi-label">Suggestion</span>
                  <strong className="p4-kpi-value">
                    {featureLabel(String(exportedEvidence.feature_id))}
                  </strong>
                  <span className="p4-kpi-sub">
                    {String(exportedEvidence.input_summary || "")}
                  </span>
                </div>
                <div className="p4-kpi-card">
                  <span className="p4-kpi-label">Freshness &amp; privacy</span>
                  <strong className="p4-kpi-value">
                    {exportedEvidence.is_stale_against_current_ledger
                      ? "Out of date"
                      : "Up to date with your books"}
                  </strong>
                  <span className="p4-kpi-sub">
                    Sensitive details hidden:{" "}
                    {exportedEvidence.redaction_applied ? "Yes" : "No"}
                  </span>
                </div>
              </div>
            )}
          </section>

          <section
            className="panel settings-section"
            aria-label="Assistant usage settings"
          >
            <h2>Usage limits &amp; pause control</h2>
            <p className="muted">
              Set a monthly spending limit or pause AI suggestions at any time
              (for example during an audit). Regular bookkeeping continues
              working either way. Used this month:{" "}
              {formatUsdShort(data?.controls?.used_cost_usd)}.
            </p>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                updateControlsMutation.mutate();
              }}
            >
              <Field label="Monthly spending limit (USD)">
                <input
                  value={ctrlBudgetUsd}
                  onChange={(e) => setCtrlBudgetUsd(e.target.value)}
                  placeholder="e.g. 25.00"
                  required
                />
              </Field>
              <Field label="Reason for pausing (optional)">
                <input
                  value={ctrlPauseReason}
                  onChange={(e) => setCtrlPauseReason(e.target.value)}
                  placeholder="e.g. Year-end review in progress"
                />
              </Field>
              <label className="p4-check-row">
                <input
                  type="checkbox"
                  checked={ctrlPaused}
                  onChange={(e) => setCtrlPaused(e.target.checked)}
                />
                <span>
                  Pause AI suggestions for this workspace (manual work is not
                  affected)
                </span>
              </label>
              <div className="p4-inline-actions">
                <Button type="submit" busy={updateControlsMutation.isPending}>
                  Save settings
                </Button>
              </div>
            </form>

            <details className="p3-advanced-details">
              <summary>Advanced limits, audit checks &amp; quality log</summary>
              <div className="form-grid" style={{ marginTop: "16px" }}>
                <Field label="Monthly usage allowance (units)">
                  <input
                    type="number"
                    value={ctrlTokenBudget}
                    onChange={(e) => setCtrlTokenBudget(e.target.value)}
                  />
                </Field>
                <Field label="Simultaneous requests allowed">
                  <input
                    type="number"
                    value={ctrlMaxConcurrent}
                    onChange={(e) => setCtrlMaxConcurrent(e.target.value)}
                  />
                </Field>
              </div>

              <h3 style={{ marginTop: "24px" }}>
                Records checked by the assistant
              </h3>
              <Table
                caption="Records checked by the assistant"
                emptyText="No data checks logged yet."
                rows={data?.tool_audit_logs || []}
                rowKey={(t) => t.id}
                columns={[
                  { label: "Check", render: (t) => humanize(t.tool_name) },
                  { label: "Records read", render: (t) => String(t.row_count) },
                  {
                    label: "Unsafe text filtered",
                    render: (t) =>
                      t.untrusted_content_sanitized ? "Filtered" : "Clean",
                  },
                  { label: "When", render: (t) => formatWhen(t.executed_at) },
                ]}
              />

              <h3 style={{ marginTop: "24px" }}>Quality &amp; accuracy log</h3>
              <p className="muted">
                Record accuracy test runs for internal review. Practice data and
                real-world samples are tracked separately.
              </p>
              <form
                className="form-grid"
                onSubmit={(e) => {
                  e.preventDefault();
                  evaluateDatasetMutation.mutate();
                }}
              >
                <Field label="Test set name">
                  <input
                    value={datasetKey}
                    onChange={(e) => setDatasetKey(e.target.value)}
                    placeholder="e.g. november-bank-matching-sample"
                    required
                  />
                </Field>
                <Field label="Type of sample">
                  <select
                    value={datasetCohort}
                    onChange={(e) =>
                      setDatasetCohort(
                        e.target.value as
                          | "synthetic_development"
                          | "representative_deidentified",
                      )
                    }
                  >
                    <option value="representative_deidentified">
                      Real-world sample (personal details removed)
                    </option>
                    <option value="synthetic_development">Practice data</option>
                  </select>
                </Field>
                <Field label="How results were generated">
                  <select
                    value={datasetProvenance}
                    onChange={(e) =>
                      setDatasetProvenance(
                        e.target.value as
                          | "actual_groq_model_execution"
                          | "deterministic_engine_execution"
                          | "supplied_prediction",
                      )
                    }
                  >
                    <option value="actual_groq_model_execution">
                      Live assistant run
                    </option>
                    <option value="deterministic_engine_execution">
                      Built-in accounting rules
                    </option>
                    <option value="supplied_prediction">
                      Entered manually (practice only)
                    </option>
                  </select>
                </Field>
                <Field label="Assistant tool">
                  <select
                    value={datasetFeatureId}
                    onChange={(e) =>
                      setDatasetFeatureId(e.target.value as BackendFeatureCode)
                    }
                  >
                    {featureOptions.map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Accuracy when flagged (0 to 1)">
                  <input
                    value={datasetPrecision}
                    onChange={(e) => setDatasetPrecision(e.target.value)}
                    required
                  />
                </Field>
                <Field label="Issues caught (0 to 1)">
                  <input
                    value={datasetRecall}
                    onChange={(e) => setDatasetRecall(e.target.value)}
                    required
                  />
                </Field>
                <Field label="Sources cited accurately (0 to 1)">
                  <input
                    value={datasetCitationRate}
                    onChange={(e) => setDatasetCitationRate(e.target.value)}
                    required
                  />
                </Field>
                <div className="p4-inline-actions">
                  <Button
                    type="submit"
                    busy={evaluateDatasetMutation.isPending}
                  >
                    Save quality check
                  </Button>
                </div>
              </form>

              <Table
                caption="Quality check history"
                emptyText="No quality checks recorded yet."
                rows={data?.evaluation_datasets || []}
                rowKey={(d) => d.id}
                columns={[
                  {
                    label: "Test set",
                    render: (d) =>
                      `${d.dataset_key} (${featureLabel(d.feature_id)})`,
                  },
                  {
                    label: "Sample type",
                    render: (d) => <Badge>{humanize(d.dataset_cohort)}</Badge>,
                  },
                  {
                    label: "Method",
                    render: (d) => (
                      <Badge>{humanize(d.execution_provenance)}</Badge>
                    ),
                  },
                  {
                    label: "Scores",
                    render: (d) =>
                      `Accuracy ${Math.round(Number(d.precision_score) * 100)}% · Caught ${Math.round(Number(d.recall_score) * 100)}%`,
                  },
                  {
                    label: "Status",
                    render: (d) => (
                      <Badge
                        tone={d.promotion_gate_passed ? "good" : "neutral"}
                      >
                        {d.promotion_gate_passed ? "Verified" : "Practice only"}
                      </Badge>
                    ),
                  },
                ]}
              />
            </details>
          </section>
        </div>
      )}

      {tab === "ai02-impact" && (
        <section
          className="panel settings-section"
          aria-label="Preview an entry"
        >
          <h2>Preview an entry before posting</h2>
          <p className="muted">
            See how a balanced journal entry would change your profit and loss,
            balance sheet, receivables, payables, and cash before saving
            anything to your books.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              impactMutation.mutate();
            }}
          >
            <Field label="Entry date">
              <input
                type="date"
                value={impactDate}
                onChange={(e) => setImpactDate(e.target.value)}
                required
              />
            </Field>
            <Field label="Description">
              <input
                value={impactMemo}
                onChange={(e) => setImpactMemo(e.target.value)}
                placeholder="e.g. Monthly software subscription accrual"
                required
              />
            </Field>
            <Field label="Debit account code">
              <input
                value={debitAccountCode}
                onChange={(e) => setDebitAccountCode(e.target.value)}
                placeholder="e.g. 6100"
                required
              />
            </Field>
            <Field label="Debit account name">
              <input
                value={debitAccountName}
                onChange={(e) => setDebitAccountName(e.target.value)}
                placeholder="e.g. Software Expense"
                required
              />
            </Field>
            <Field label="Debit account type">
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
            <Field label="Credit account code">
              <input
                value={creditAccountCode}
                onChange={(e) => setCreditAccountCode(e.target.value)}
                placeholder="e.g. 2100"
                required
              />
            </Field>
            <Field label="Credit account name">
              <input
                value={creditAccountName}
                onChange={(e) => setCreditAccountName(e.target.value)}
                placeholder="e.g. Accrued Payable"
                required
              />
            </Field>
            <Field label="Credit account type">
              <select
                value={creditKind}
                onChange={(e) =>
                  setCreditKind(e.target.value as "liability" | "asset")
                }
              >
                <option value="liability">Liability (Payable / Accrued)</option>
                <option value="asset">Asset (Cash / Bank)</option>
              </select>
            </Field>
            <Field label={`Entry amount (${b.currency})`}>
              <input
                value={impactAmount}
                onChange={(e) => setImpactAmount(e.target.value)}
                placeholder="e.g. 1250.00"
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button type="submit" busy={impactMutation.isPending}>
                Preview impact
              </Button>
            </div>
          </form>

          {impactResult && (
            <div
              className="p4-kpi-grid"
              role="region"
              aria-label="Preview results"
            >
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Net profit change</span>
                <strong className="p4-kpi-value">
                  {money(
                    String(impactResult.pnl_net_income_delta || "0.00"),
                    String(impactResult.currency || b.currency),
                  )}
                </strong>
                <span className="p4-kpi-sub">
                  Accounting basis: {humanize(String(impactResult.basis || ""))}
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Cash change</span>
                <strong className="p4-kpi-value">
                  {money(
                    String(impactResult.cash_delta || "0.00"),
                    String(impactResult.currency || b.currency),
                  )}
                </strong>
                <span className="p4-kpi-sub">
                  {impactResult.is_noncash_reclassification
                    ? "No immediate cash movement"
                    : "Updates cash balance"}
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Summary</span>
                <span className="p4-kpi-sub">
                  {String(impactResult.narrative || "")}
                </span>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "ai04-memory" && (
        <section
          className="panel settings-section"
          aria-label="Accounting rules"
        >
          <h2>Saved accounting rules</h2>
          <p className="muted">
            Teach the assistant how your business categorizes recurring vendors
            or expenses. New rules wait for an accountant or owner to approve
            them before they are applied.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              createMemoryMutation.mutate();
            }}
          >
            <Field label="Short rule key">
              <input
                value={memoryKey}
                onChange={(e) => setMemoryKey(e.target.value)}
                placeholder="e.g. cloud-hosting-policy"
                required
              />
            </Field>
            <Field label="Rule title">
              <input
                value={memoryTitle}
                onChange={(e) => setMemoryTitle(e.target.value)}
                placeholder="e.g. Monthly cloud hosting bills"
                required
              />
            </Field>
            <Field label="Accounting standard">
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
                <option value="TAX_BASIS">Tax basis</option>
              </select>
            </Field>
            <Field label="Rule strength">
              <select
                value={memoryBinding}
                onChange={(e) =>
                  setMemoryBinding(
                    e.target.value as "binding_policy" | "past_example",
                  )
                }
              >
                <option value="binding_policy">Standard business policy</option>
                <option value="past_example">Reference example</option>
              </select>
            </Field>
            <Field label="Target account code">
              <input
                value={memoryAccountCode}
                onChange={(e) => setMemoryAccountCode(e.target.value)}
                placeholder="e.g. 6100"
                required
              />
            </Field>
            <Field label="Target account name">
              <input
                value={memoryAccountName}
                onChange={(e) => setMemoryAccountName(e.target.value)}
                placeholder="e.g. Cloud Hosting Expense"
                required
              />
            </Field>
            <Field label={`Review threshold (${b.currency})`}>
              <input
                value={memoryMateriality}
                onChange={(e) => setMemoryMateriality(e.target.value)}
                placeholder="e.g. 500.00"
                required
              />
            </Field>
            <Field label="When should this rule apply?">
              <input
                value={memoryCondition}
                onChange={(e) => setMemoryCondition(e.target.value)}
                placeholder="e.g. Monthly hosting bills under $5,000 go to 6100 Cloud Hosting Expense"
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button type="submit" busy={createMemoryMutation.isPending}>
                Save rule for review
              </Button>
            </div>
          </form>

          <Table
            caption="Saved accounting rules"
            emptyText="No custom accounting rules saved yet."
            rows={data?.memories || []}
            rowKey={(m) => m.id}
            columns={[
              {
                label: "Rule",
                render: (m) => `${m.title} (v${m.version})`,
              },
              {
                label: "Standard",
                render: (m) =>
                  `${humanize(m.accounting_framework)} · ${humanize(m.binding_kind)}`,
              },
              {
                label: "When it applies",
                render: (m) => m.rule_condition,
              },
              {
                label: "Account",
                render: (m) => `${m.account_code} ${m.account_name}`,
              },
              {
                label: "Status",
                render: (m) => (
                  <Badge
                    tone={
                      m.status === "approved"
                        ? "good"
                        : m.status === "revoked"
                          ? "warning"
                          : "neutral"
                    }
                  >
                    {humanize(m.status)}
                  </Badge>
                ),
              },
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
                      Approve
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
                      Retire
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </section>
      )}

      {tab === "ai01-recon" && (
        <section className="panel settings-section" aria-label="Bank matching">
          <h2>Match an unexplained bank line</h2>
          <p className="muted">
            Enter a line from your bank statement to check for matching open
            invoices or bills, combined payments, card processing fees, partial
            payments, or duplicate imports.
          </p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              reconMutation.mutate();
            }}
          >
            <Field label="Bank statement description">
              <input
                value={reconDesc}
                onChange={(e) => setReconDesc(e.target.value)}
                placeholder="e.g. STRIPE PAYOUT - INV-101 & INV-102"
                required
              />
            </Field>
            <Field label={`Statement amount (${b.currency})`}>
              <input
                value={reconAmount}
                onChange={(e) => setReconAmount(e.target.value)}
                placeholder="e.g. 970.00"
                required
              />
            </Field>
            <Field label="Statement date">
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
              <span>This statement reference was already imported earlier</span>
            </label>
            <div className="p4-inline-actions">
              <Button type="submit" busy={reconMutation.isPending}>
                Find matching records
              </Button>
            </div>
          </form>

          <Table
            caption="Bank matching checks"
            emptyText="No bank lines checked yet."
            rows={data?.reconciliation_investigations || []}
            rowKey={(inv) => inv.id}
            columns={[
              {
                label: "Statement line",
                render: (inv) => inv.statement_description,
              },
              {
                label: "Amount",
                render: (inv) => money(inv.statement_amount, inv.currency),
              },
              {
                label: "Finding",
                render: (inv) => <Badge>{humanize(inv.classification)}</Badge>,
              },
              {
                label: "Difference / fee",
                render: (inv) =>
                  `Diff: ${inv.deterministic_delta} · Fee: ${inv.fee_amount}`,
              },
              { label: "Explanation", render: (inv) => inv.explanation },
              {
                label: "Status",
                render: (inv) => <Badge>{humanize(inv.status)}</Badge>,
              },
            ]}
          />
        </section>
      )}

      {tab === "ai03-clarify" && (
        <section
          className="panel settings-section"
          aria-label="Client requests"
        >
          <h2>Follow up on missing documents</h2>
          <p className="muted">
            Prepare a clear message asking a client or teammate for a missing
            receipt, invoice, or payment explanation. Duplicate requests for the
            same item are prevented automatically.
          </p>
          <ErrorNotice error={createClarificationMutation.error} />
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              createClarificationMutation.mutate();
            }}
          >
            <Field label="What do you need help with?">
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
                  Missing invoice or receipt
                </option>
                <option value="bank_row">Unexplained bank transaction</option>
                <option value="unmatched_payment">Unmatched payment</option>
                <option value="contract_variance">
                  Invoice differs from contract
                </option>
              </select>
            </Field>
            <Field label="Item reference">
              <input
                value={clarRef}
                onChange={(e) => setClarRef(e.target.value)}
                placeholder="e.g. BILL-2026-884"
                required
              />
            </Field>
            <Field label="Contact name or email">
              <input
                value={clarRecipient}
                onChange={(e) => setClarRecipient(e.target.value)}
                placeholder="e.g. billing@client.com"
                required
              />
            </Field>
            <Field label="Send via">
              <select
                value={clarChannel}
                onChange={(e) =>
                  setClarChannel(
                    e.target.value as "email" | "whatsapp" | "slack",
                  )
                }
              >
                <option value="email">Email</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="slack">Slack</option>
              </select>
            </Field>
            <Field label="Details for the recipient">
              <input
                value={clarContext}
                onChange={(e) => setClarContext(e.target.value)}
                placeholder="e.g. Please share the PDF invoice and service dates for November."
                required
              />
            </Field>
            <div className="p4-inline-actions">
              <Button
                type="submit"
                busy={createClarificationMutation.isPending}
              >
                Create document request
              </Button>
            </div>
          </form>

          <Table
            caption="Open client requests"
            emptyText="No client document requests yet."
            rows={data?.client_clarifications || []}
            rowKey={(c) => c.id}
            columns={[
              {
                label: "Item",
                render: (c) =>
                  `${humanize(c.exception_kind)}: ${c.exception_ref}`,
              },
              {
                label: "Recipient",
                render: (c) => `${c.recipient_key} (${humanize(c.channel)})`,
              },
              {
                label: "Still needed",
                render: (c) =>
                  c.remaining_gaps.length > 0
                    ? c.remaining_gaps.join(", ")
                    : "All documents received",
              },
              {
                label: "Status",
                render: (c) => <Badge>{humanize(c.status)}</Badge>,
              },
              {
                label: "Record reply",
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
                      Mark received
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
                      Flag issue
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
          className="panel settings-section"
          aria-label="Month-end and priorities"
        >
          <h2>Month-end readiness &amp; workspace priorities</h2>
          <p className="muted">
            Check whether a period is ready to close and review open items
            across your businesses. Final period sign-off always stays with an
            authorized accountant.
          </p>
          <div className="form-grid">
            <Field label="Accounting period (YYYY-MM)">
              <input
                value={closePeriodLabel}
                onChange={(e) => setClosePeriodLabel(e.target.value)}
                placeholder="e.g. 2026-11"
              />
            </Field>
            <div className="p4-inline-actions">
              <Button
                busy={closeAssistantMutation.isPending}
                onClick={() => closeAssistantMutation.mutate()}
              >
                Check month-end readiness
              </Button>
              <Button
                variant="secondary"
                busy={portfolioPlannerMutation.isPending}
                onClick={() => portfolioPlannerMutation.mutate()}
              >
                Rank my workspaces
              </Button>
            </div>
          </div>

          {closeReport && (
            <div
              className="p4-kpi-grid"
              role="region"
              aria-label="Month-end readiness results"
            >
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Readiness score</span>
                <strong className="p4-kpi-value">
                  {String(closeReport.readiness_score_pct)}%
                </strong>
                <span className="p4-kpi-sub">
                  Period: {String(closeReport.period_label)} · Final sign-off:
                  Accountant required
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Checklist summary</span>
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
              aria-label="Workspace priority results"
            >
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Workspaces reviewed</span>
                <strong className="p4-kpi-value">
                  {String(portfolioReport.authorized_businesses_evaluated)}
                </strong>
                <span className="p4-kpi-sub">
                  Currencies are grouped separately and never mixed.
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Today&apos;s focus</span>
                <span className="p4-kpi-sub">
                  {String(portfolioReport.daily_briefing)}
                </span>
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "ai06-voice" && (
        <section className="panel settings-section" aria-label="Voice entry">
          <h2>Record a bill or expense by voice</h2>
          <p className="muted">
            Speak or type in English, Urdu, or a mix of both. Voice notes never
            post directly to your books — you confirm the vendor, date, and
            amount first, and we create a draft bill for review.
          </p>
          <ErrorNotice
            error={voiceSessionMutation.error || confirmVoiceMutation.error}
          />
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              voiceSessionMutation.mutate({ regenerate: false });
            }}
          >
            <Field label="Spoken language">
              <select
                value={voiceLocale}
                onChange={(e) =>
                  setVoiceLocale(
                    e.target.value as "en_US" | "ur_PK" | "en_ur_mixed",
                  )
                }
              >
                <option value="en_ur_mixed">English &amp; Urdu (mixed)</option>
                <option value="en_US">English</option>
                <option value="ur_PK">Urdu</option>
              </select>
            </Field>
            <Field label="What would you like to record?">
              <input
                value={voiceTranscriptInput}
                onChange={(e) => setVoiceTranscriptInput(e.target.value)}
                placeholder="e.g. Record electricity bill from K-Electric for 850.00 USD due today"
                required={!voiceAudioBase64}
              />
            </Field>
            <Field label="Or upload an audio note (WAV, WebM, MP3)">
              <input
                type="file"
                accept="audio/*"
                aria-label="Upload Audio Recording"
                onChange={(e) =>
                  void handleAudioFileUpload(e.target.files?.[0] || null)
                }
              />
            </Field>
            <label className="p4-check-row">
              <input
                type="checkbox"
                checked={voiceRequestLiveKit}
                onChange={(e) => setVoiceRequestLiveKit(e.target.checked)}
              />
              <span>Start a live voice session (when enabled)</span>
            </label>
            <div
              className={`voice-recorder-bar${isRecordingMic ? " is-recording" : ""}`}
              role="status"
              aria-live="polite"
            >
              <span className="voice-recorder-status">
                {isRecordingMic ? (
                  <>
                    <span className="recording-dot" aria-hidden="true" />
                    Recording live · {formatRecordingDuration(recordingSeconds)}
                  </>
                ) : voiceAudioBase64 ? (
                  <>
                    Audio clip ready (
                    {formatRecordingDuration(recordingSeconds)}) — click
                    Understand my instruction
                  </>
                ) : (
                  <>
                    Microphone ready — click Record with microphone to capture a
                    spoken note
                  </>
                )}
              </span>
              {isRecordingMic && (
                <Button
                  type="button"
                  variant="danger"
                  onClick={() => void handleToggleMicRecording()}
                >
                  Stop recording
                </Button>
              )}
            </div>
            {voiceMicError && (
              <div className="notice error" role="alert">
                <div>{voiceMicError}</div>
              </div>
            )}
            <div className="p4-inline-actions">
              <Button
                type="button"
                variant={isRecordingMic ? "danger" : "secondary"}
                onClick={() => void handleToggleMicRecording()}
              >
                {isRecordingMic ? "Stop recording" : "Record with microphone"}
              </Button>
              <Button type="submit" busy={voiceSessionMutation.isPending}>
                Understand my instruction
              </Button>
            </div>
          </form>

          {voiceAudioPreviewUrl && (
            <div
              className="p4-inline-actions"
              role="region"
              aria-label="Recorded Audio Playback"
              style={{ marginTop: "14px" }}
            >
              <audio
                controls
                src={voiceAudioPreviewUrl}
                aria-label="Voice Accounting Audio Playback"
              />
            </div>
          )}

          {activeVoiceReply && (
            <div
              className="voice-reply-card"
              role="region"
              aria-label="Saved voice reply"
            >
              <h3>Saved reply for this recording</h3>
              <p style={{ margin: 0 }}>{activeVoiceReply.ttsReadbackText}</p>
              <div className="voice-reply-meta">
                <span>
                  <strong>Transcript:</strong>{" "}
                  {activeVoiceReply.correctedTranscript}
                </span>
                <span>
                  <strong>Vendor / party:</strong>{" "}
                  {activeVoiceReply.extractedEntities.party_name || "Supplier"}
                </span>
                <span>
                  <strong>Amount:</strong>{" "}
                  {money(
                    activeVoiceReply.extractedEntities.amount || "0.00",
                    activeVoiceReply.extractedEntities.currency || b.currency,
                  )}
                </span>
              </div>
              <div className="p4-inline-actions">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    speakReadback(activeVoiceReply.ttsReadbackText)
                  }
                >
                  Read aloud
                </Button>
                {activeVoiceReply.sessionId && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={confirmVoiceMutation.isPending}
                    onClick={() =>
                      confirmVoiceMutation.mutate({
                        sessionId: activeVoiceReply.sessionId,
                        transcript: activeVoiceReply.correctedTranscript,
                        partyName:
                          activeVoiceReply.extractedEntities.party_name ||
                          "Supplier",
                        amount:
                          activeVoiceReply.extractedEntities.amount || "100.00",
                        currency:
                          activeVoiceReply.extractedEntities.currency ||
                          b.currency,
                      })
                    }
                  >
                    Confirm &amp; create draft
                  </Button>
                )}
                <Button
                  type="button"
                  variant="secondary"
                  busy={voiceSessionMutation.isPending}
                  onClick={() =>
                    voiceSessionMutation.mutate({ regenerate: true })
                  }
                >
                  Regenerate reply
                </Button>
              </div>
            </div>
          )}

          <Table
            caption="Voice notes awaiting confirmation"
            emptyText="No voice notes recorded yet."
            rows={data?.voice_sessions || []}
            rowKey={(vs) => vs.id}
            columns={[
              {
                label: "Transcript",
                render: (vs) => vs.corrected_transcript,
              },
              {
                label: "Extracted details",
                render: (vs) =>
                  `${vs.extracted_entities.party_name || "Supplier"} · ${money(vs.extracted_entities.amount || "0.00", vs.extracted_entities.currency || b.currency)}`,
              },
              {
                label: "Please verify",
                render: (vs) =>
                  vs.uncertain_fields.length > 0
                    ? vs.uncertain_fields.map(humanize).join(", ")
                    : "Ready to confirm",
              },
              {
                label: "Status",
                render: (vs) => (
                  <Badge
                    tone={vs.critical_fields_confirmed ? "good" : "neutral"}
                  >
                    {vs.linked_draft_id ? "Draft created" : humanize(vs.status)}
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
                      Read aloud
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
                            vs.extracted_entities.party_name || "Supplier",
                          amount: vs.extracted_entities.amount || "100.00",
                          currency:
                            vs.extracted_entities.currency || b.currency,
                        })
                      }
                    >
                      Confirm &amp; create draft
                    </Button>
                  </div>
                ),
              },
            ]}
          />
        </section>
      )}

      {tab === "ai07-contracts-cash" && (
        <div className="stack">
          <section
            className="panel settings-section"
            aria-label="Contract verification"
          >
            <h2>Check an invoice against a contract</h2>
            <p className="muted">
              Compare contracted pricing caps against what was billed and paid
              to spot overcharges, duplicate billing, or unapproved milestones.
            </p>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                contractVerificationMutation.mutate();
              }}
            >
              <Field label="Contract name or number">
                <input
                  value={contractRef}
                  onChange={(e) => setContractRef(e.target.value)}
                  placeholder="e.g. MSA-2026-09"
                  required
                />
              </Field>
              <Field label="Vendor or customer">
                <input
                  value={contractVendor}
                  onChange={(e) => setContractVendor(e.target.value)}
                  placeholder="e.g. Acme Cloud Services"
                  required
                />
              </Field>
              <Field label="Pricing terms from contract">
                <input
                  value={contractClause}
                  onChange={(e) => setContractClause(e.target.value)}
                  placeholder="e.g. Monthly fee capped at $2,000 unless a signed amendment is attached."
                  required
                />
              </Field>
              <Field label={`Contracted amount (${b.currency})`}>
                <input
                  value={contractedAmt}
                  onChange={(e) => setContractedAmt(e.target.value)}
                  required
                />
              </Field>
              <Field label={`Invoiced amount (${b.currency})`}>
                <input
                  value={invoicedAmt}
                  onChange={(e) => setInvoicedAmt(e.target.value)}
                  required
                />
              </Field>
              <Field label={`Amount already paid (${b.currency})`}>
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
                  Compare contract &amp; invoice
                </Button>
              </div>
            </form>

            <Table
              caption="Contract checks"
              emptyText="No contracts compared yet."
              rows={data?.contract_verifications || []}
              rowKey={(cv) => cv.id}
              columns={[
                {
                  label: "Contract",
                  render: (cv) =>
                    `${cv.contract_reference} (${cv.vendor_or_customer})`,
                },
                {
                  label: "Agreed",
                  render: (cv) => money(cv.contracted_amount, cv.currency),
                },
                {
                  label: "Billed / paid",
                  render: (cv) =>
                    `Billed ${cv.invoiced_amount} · Paid ${cv.paid_amount}`,
                },
                {
                  label: "Difference",
                  render: (cv) =>
                    `${money(cv.discrepancy_amount, cv.currency)} (${humanize(cv.discrepancy_kind)})`,
                },
                {
                  label: "Recommended next step",
                  render: (cv) => cv.proposed_action,
                },
                {
                  label: "Status",
                  render: (cv) => <Badge>{humanize(cv.status)}</Badge>,
                },
              ]}
            />
          </section>

          <section
            className="panel settings-section"
            aria-label="Cash-flow what-if forecast"
          >
            <h2>13-week cash-flow forecast</h2>
            <p className="muted">
              Test what-if questions — such as slower customer collections, an
              equipment purchase, or higher monthly costs — without changing
              your actual books.
            </p>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                cashScenarioMutation.mutate();
              }}
            >
              <Field label="Scenario name">
                <input
                  value={scenarioName}
                  onChange={(e) => setScenarioName(e.target.value)}
                  placeholder="e.g. Two-week collection delay + new laptops"
                  required
                />
              </Field>
              <Field label="What are you testing?">
                <input
                  value={scenarioPrompt}
                  onChange={(e) => setScenarioPrompt(e.target.value)}
                  placeholder="e.g. What if customers pay 2 weeks later and we spend $8,000 on equipment?"
                  required
                />
              </Field>
              <Field label="Customer payment delay (weeks)">
                <input
                  type="number"
                  min={0}
                  max={12}
                  value={delayWeeks}
                  onChange={(e) => setDelayWeeks(e.target.value)}
                  required
                />
              </Field>
              <Field label={`One-time purchase in week 1 (${b.currency})`}>
                <input
                  value={equipmentCapex}
                  onChange={(e) => setEquipmentCapex(e.target.value)}
                  required
                />
              </Field>
              <Field label="Change in recurring costs (%)">
                <input
                  value={costChangePct}
                  onChange={(e) => setCostChangePct(e.target.value)}
                  required
                />
              </Field>
              <div className="p4-inline-actions">
                <Button type="submit" busy={cashScenarioMutation.isPending}>
                  Run 13-week forecast
                </Button>
              </div>
            </form>

            <Table
              caption="Saved cash-flow scenarios"
              emptyText="No cash-flow scenarios run yet."
              rows={data?.cash_scenarios || []}
              rowKey={(cs) => cs.id}
              columns={[
                { label: "Scenario", render: (cs) => cs.scenario_name },
                {
                  label: "Baseline ending cash",
                  render: (cs) => money(cs.baseline_ending_cash, cs.currency),
                },
                {
                  label: "Scenario ending cash",
                  render: (cs) => money(cs.scenario_ending_cash, cs.currency),
                },
                {
                  label: "Net difference",
                  render: (cs) => money(cs.net_cash_delta, cs.currency),
                },
                {
                  label: "Lowest weekly cash",
                  render: (cs) => money(cs.lowest_weekly_cash, cs.currency),
                },
                {
                  label: "Tight weeks",
                  render: (cs) => String(cs.shortfall_weeks),
                },
              ]}
            />
          </section>
        </div>
      )}
    </div>
  );
}
