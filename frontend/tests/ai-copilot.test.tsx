import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, queryClient } from "../src/api/runtime";
import { AccessContext, BusinessContext } from "../src/features/context";
import { AICopilotHub } from "../src/features/ai-copilot";

const mockBusiness = {
  id: "42132f25-4948-45c0-9142-8def637b0a9c",
  name: "Acme Test Corp",
  currency: "USD",
  currency_precision: 2,
  timezone: "UTC",
  basis: "accrual" as const,
  version: 3,
  policy_version: 1,
  fiscal_month: 1,
  jurisdiction: "US",
  p2_projects_available: true,
  p2_insights_available: true,
  p2_retention_available: true,
  p2_audit_available: true,
  p2_reconciliation_available: true,
  p2_micro_close_available: true,
  p2_prepaids_available: true,
};

describe("AI accounting assistant UI", () => {
  it("renders plain-language summary, clean tabs, and voice audio controls without internal labels", async () => {
    queryClient.clear();
    const getSpy = vi.spyOn(api, "GET").mockImplementation((async (
      url: string,
    ) => {
      if (url === "/api/v1/businesses/{business_id}/ai/overview") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            foundation: {
              wave: "W0_TO_W6_ACTIVE",
              groq_configured: true,
              active_provider: "groq",
              llm_model: "llama-3.3-70b-versatile",
              stt_model: "whisper-large-v3-turbo",
              tts_mode: "browser_speech_synthesis",
              livekit_enabled: false,
              livekit_mode: "groq_whisper_and_browser_tts",
              business_currency: "USD",
              business_basis: "accrual",
              business_version: 3,
              policy_version: 1,
              read_only_tools_enforced: true,
              prompt_injection_shield_active: true,
              human_approval_required: true,
            },
            runs: [
              {
                id: "11111111-2222-3333-4444-555555555555",
                feature_id: "AI-02",
                wave: "W1",
                provider: "groq",
                model_name: "llama-3.3-70b-versatile",
                status: "completed",
                input_summary: "Entry preview (1250.00 USD): Cloud hosting",
                confidence_score: "1.0000",
                uncertainty_flags: [],
                snapshot_hash: "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
                business_version: 3,
                prompt_tokens: 110,
                completion_tokens: 35,
                total_tokens: 145,
                latency_ms: 142,
                estimated_cost_usd: "0.000093",
                approval_state: "pending_review",
                approval_content_hash: "9988776655443322",
                citations: [
                  {
                    source_type: "ledger_snapshot",
                    reference: "business:acme:v3",
                  },
                ],
                tool_invocations: [
                  {
                    tool_name: "get_account_balances",
                    row_count: 8,
                    result_bytes: 640,
                    duration_ms: 4,
                    untrusted_content_sanitized: false,
                  },
                ],
                created_at: "2026-11-26T12:00:00Z",
              },
            ],
            tool_audit_logs: [
              {
                id: "22222222-3333-4444-5555-666666666666",
                run_id: "11111111-2222-3333-4444-555555555555",
                tool_name: "get_account_balances",
                parameters_hash: "abc123def456",
                row_count: 8,
                result_bytes: 640,
                untrusted_content_sanitized: false,
                duration_ms: 4,
                executed_at: "2026-11-26T12:00:00Z",
              },
            ],
            memories: [],
            reconciliation_investigations: [],
            client_clarifications: [],
            voice_sessions: [],
            contract_verifications: [],
            cash_scenarios: [],
            controls: {
              ai_enabled: true,
              operator_paused: false,
              pause_reason: "",
              monthly_budget_usd: "25.000000",
              monthly_token_budget: 500000,
              max_concurrent_runs: 4,
              used_tokens: 145,
              used_cost_usd: "0.000093",
              manual_workflows_unaffected: true,
            },
            evaluation_datasets: [
              {
                id: "33333333-4444-5555-6666-777777777777",
                dataset_key: "recon-deid-benchmark-v1",
                dataset_cohort: "representative_deidentified",
                execution_provenance: "actual_groq_model_execution",
                feature_id: "AI-01",
                sample_count: 40,
                precision_score: "0.9600",
                recall_score: "0.9400",
                f1_score: "0.9499",
                citation_validity_rate: "0.9950",
                promotion_gate_passed: true,
                notes: "De-identified benchmark",
                created_at: "2026-11-26T12:00:00Z",
              },
            ],
          },
        };
      }
      throw new Error(`Unexpected GET ${url}`);
    }) as never);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AccessContext.Provider
            value={{
              user_id: "d1c40361-f7c0-47c2-8df2-c4acb6ebfab6",
              role: "accountant",
              active: true,
              can_post: true,
              can_invite: true,
              version: 1,
            }}
          >
            <BusinessContext.Provider value={mockBusiness}>
              <AICopilotHub />
            </BusinessContext.Provider>
          </AccessContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(
        screen.getByRole("heading", {
          name: /AI accounting assistant/i,
        }),
      ).toBeInTheDocument();
    });

    expect(screen.getByText("You stay in control")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: /Recent suggestions & approvals/i,
      }),
    ).toBeInTheDocument();

    const user = userEvent.setup();

    await user.click(screen.getByRole("tab", { name: /^Entry preview$/i }));
    expect(
      await screen.findByRole("heading", {
        name: /Preview an entry before posting/i,
      }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /^Accounting rules$/i }));
    expect(
      await screen.findByRole("heading", {
        name: /Saved accounting rules/i,
      }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /^Bank matching$/i }));
    expect(
      await screen.findByRole("heading", {
        name: /Match an unexplained bank line/i,
      }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /^Client requests$/i }));
    expect(
      await screen.findByRole("heading", {
        name: /Follow up on missing documents/i,
      }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("tab", { name: /Month-end & priorities/i }),
    );
    expect(
      await screen.findByRole("heading", {
        name: /Month-end readiness & workspace priorities/i,
      }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /^Voice entry$/i }));
    expect(
      await screen.findByRole("heading", {
        name: /Record a bill or expense by voice/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Record with microphone/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(/Upload Audio Recording/i),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("tab", { name: /Contracts & cash forecast/i }),
    );
    expect(
      await screen.findByRole("heading", {
        name: /Check an invoice against a contract/i,
      }),
    ).toBeInTheDocument();

    getSpy.mockRestore();
  });
});
