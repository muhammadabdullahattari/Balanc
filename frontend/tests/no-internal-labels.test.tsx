import { render, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, queryClient } from "../src/api/runtime";
import { AccessContext, BusinessContext } from "../src/features/context";
import { AICopilotHub, type AICopilotTab } from "../src/features/ai-copilot";
import {
  MultichannelReminders,
  P3AutomationHub,
} from "../src/features/p3-automation";
import { QuickBooksImport } from "../src/features/quickbooks-import";

const FORBIDDEN_INTERNAL_LABELS = [
  /\bP[1-4]\b/,
  /\bP[1-4]-[A-Z0-9]+\b/,
  /\bFeature [A-Z]\b/,
  /\bAI-\d{2}\b/,
  /\bW0\b/,
  /\bPhase 3\b/i,
  /\bSANDBOX\b/,
  /\bFernet\b/i,
  /\bTOTP\b/,
  /\bHTTP 4\d\d\b/,
  /\bIAS 21\b/,
  /\bNACHA\b/,
  /\bMT103\b/,
  /\bllama-3\.3/i,
  /\bwhisper-large/i,
  /\b25\.000000\b/,
  /\bus-east-va-cell-1\b/,
  /\beu-central-fra-cell-1\b/,
  /\bpending_review\b/,
];

const mockBusiness = {
  id: "42132f25-4948-45c0-9142-8def637b0a9c",
  name: "Acme Books",
  currency: "USD",
  currency_precision: 2,
  timezone: "UTC",
  basis: "accrual" as const,
  version: 2,
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

function assertNoInternalLabels(textContent: string, screenName: string) {
  for (const pattern of FORBIDDEN_INTERNAL_LABELS) {
    expect(
      textContent,
      `Found forbidden internal label ${pattern} on screen "${screenName}"`,
    ).not.toMatch(pattern);
  }
}

describe("User-facing copy hygiene (zero internal/developer labels)", () => {
  it("never renders ticket codes, phase prefixes, model IDs, or developer jargon across AI, Automation, WhatsApp, Slack, and QuickBooks screens", async () => {
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
              business_version: 2,
              policy_version: 1,
              read_only_tools_enforced: true,
              prompt_injection_shield_active: true,
              human_approval_required: true,
            },
            controls: {
              ai_enabled: true,
              operator_paused: false,
              pause_reason: "",
              monthly_budget_usd: "25.000000",
              monthly_token_budget: 500000,
              max_concurrent_runs: 4,
              used_tokens: 0,
              used_cost_usd: "0.000000",
              manual_workflows_unaffected: true,
            },
            runs: [],
            tool_audit_logs: [],
            memories: [],
            reconciliation_investigations: [],
            client_clarifications: [],
            voice_sessions: [],
            contract_verifications: [],
            cash_scenarios: [],
            evaluation_datasets: [],
          },
        };
      }
      return {
        response: new Response(null, { status: 200 }),
        data: {
          channels_status: {
            whatsapp_cloud_connected: false,
            whatsapp_mode: "sandbox",
            slack_connected: false,
            slack_mode: "sandbox",
            email_fallback_available: true,
          },
          preferences: [],
          reminders: [],
          connections: [],
          ingestions: [],
          controls: {
            kill_switch_active: false,
            kill_switch_reason: "",
            single_payment_limit: "50000.00",
            dual_approval_threshold: "5000.00",
            default_rail: "stripe_live",
            stripe_live_available: false,
          },
          beneficiaries: [],
          payments: [],
          reconciliations: [],
          recent_bank_feed_rows: [],
          feed_status: {
            stripe_financial_connections_live: false,
            provider_mode: "sandbox",
          },
          instructions: [],
          keys: [],
          runs: [],
          workflows: [],
          migrations: [],
          batches: [],
          drills: [],
          items: [],
          movements: [],
          entities: [],
        },
      };
    }) as never);

    const wrap = (node: React.ReactNode) =>
      render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <AccessContext.Provider
              value={{
                user_id: "d1c40361-f7c0-47c2-8df2-c4acb6ebfab6",
                role: "owner",
                active: true,
                can_post: true,
                can_invite: true,
                version: 1,
              }}
            >
              <BusinessContext.Provider value={mockBusiness}>
                {node}
              </BusinessContext.Provider>
            </AccessContext.Provider>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    const aiTabs: AICopilotTab[] = [
      "w0-evidence",
      "ai02-impact",
      "ai04-memory",
      "ai01-recon",
      "ai03-clarify",
      "ai05-close-portfolio",
      "ai06-voice",
      "ai07-contracts-cash",
    ];
    for (const tab of aiTabs) {
      const view = wrap(<AICopilotHub initialTab={tab} />);
      await waitFor(() =>
        expect(view.container.querySelector("h1")).toBeTruthy(),
      );
      assertNoInternalLabels(view.container.textContent || "", `AI:${tab}`);
      view.unmount();
    }

    const automationTabs = [
      "reminders",
      "portals",
      "payments",
      "sla",
      "payouts",
      "voice",
      "encryption",
      "ai-benchmarks",
      "accounting-expansion",
      "enterprise-sso",
      "p4-operations",
      "p4-migrations",
      "p4-bulk-ingestion",
      "p4-disaster-recovery",
      "p4-inventory",
      "p4-group-consolidation",
    ] as const;

    for (const tab of automationTabs) {
      const view = wrap(<P3AutomationHub initialTab={tab} />);
      await waitFor(() =>
        expect(view.container.querySelector("h1")).toBeTruthy(),
      );
      assertNoInternalLabels(
        view.container.textContent || "",
        `Automation:${tab}`,
      );
      view.unmount();
    }

    // Verify WhatsApp-only and Slack-only views render independently
    const waOnly = wrap(<MultichannelReminders channelFilter="whatsapp" />);
    await waitFor(() =>
      expect(waOnly.container.textContent).toContain("Connect WhatsApp"),
    );
    expect(waOnly.container.textContent).not.toContain("Connect Slack");
    assertNoInternalLabels(waOnly.container.textContent || "", "WhatsApp-only");
    waOnly.unmount();

    const slackOnly = wrap(<MultichannelReminders channelFilter="slack" />);
    await waitFor(() =>
      expect(slackOnly.container.textContent).toContain("Connect Slack"),
    );
    expect(slackOnly.container.textContent).not.toContain("Connect WhatsApp");
    assertNoInternalLabels(slackOnly.container.textContent || "", "Slack-only");
    slackOnly.unmount();

    const qb = wrap(<QuickBooksImport />);
    await waitFor(() => expect(qb.container.textContent).toBeTruthy());
    assertNoInternalLabels(qb.container.textContent || "", "QuickBooksImport");
    qb.unmount();

    getSpy.mockRestore();
  });
});
