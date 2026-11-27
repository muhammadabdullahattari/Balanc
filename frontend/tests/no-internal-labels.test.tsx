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
import { Settings } from "../src/features/settings";
import { CleanupControls } from "../src/features/cleanup";
import { ClosePackages } from "../src/features/close-packages";
import { FinancialAudit } from "../src/features/financial-audit";
import { RetentionControls } from "../src/features/retention";

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
  /\bUUID\b/,
  /\bSMTP\b/,
  /\bPolicy version\b/,
  /\bllama-3\.3/i,
  /\bwhisper-large/i,
  /\b25\.000000\b/,
  /\bus-east-va-cell-1\b/,
  /\beu-central-fra-cell-1\b/,
  /\bpending_review\b/,
  /\ufffd/,
  /\u00e2\u20ac/,
  /\u00c2\u00b7/,
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
  it("never renders ticket codes, phase prefixes, model IDs, mojibake, or developer jargon across AI, Automation, Settings, Retention, Cleanup, Close, and QuickBooks screens", async () => {
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
      if (url.includes("/retention/cleanup/")) {
        return {
          response: new Response(null, { status: 200 }),
          data: [],
        };
      }
      if (url.endsWith("/retention")) {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            default_years: 7,
            policies: [
              {
                id: "pol-1",
                category: "trade_documents",
                jurisdiction: "US",
                years: 7,
                version: 1,
                reference: "IRS Rev. Proc.",
              },
            ],
            holds: [],
          },
        };
      }
      if (url.endsWith("/memberships")) {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            items: [
              {
                user_id: "d1c40361-f7c0-47c2-8df2-c4acb6ebfab6",
                role: "owner",
                active: true,
                can_post: true,
                can_invite: true,
              },
            ],
          },
        };
      }
      if (url === "/api/v1/businesses/{business_id}/audits/graph") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            mermaid: "graph TD;",
            nodes: ["validate_request", "load_data", "polish_report"],
            tools: [
              "evaluate_accounting_rules",
              "reconcile_balances_and_continuity",
            ],
          },
        };
      }
      if (url === "/api/v1/businesses/{business_id}/audits") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            items: [
              {
                id: "90a32f25-4948-45c0-9142-8def637b0a9c",
                audit_type: "monthly",
                trigger_source: "manual",
                status: "completed",
                job_id: null,
                period_start: "2026-11-01",
                period_end: "2026-11-30",
                severity_summary: {
                  critical: 0,
                  high: 1,
                  medium: 0,
                  low: 0,
                  total: 1,
                  health_status: "attention_needed",
                },
                executive_summary:
                  "Checked November 2026 records and found 1 item to review.",
                created_at: "2026-11-26T12:00:00Z",
              },
            ],
            next_cursor: null,
          },
        };
      }
      if (url === "/api/v1/businesses/{business_id}/audits/{audit_id}") {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            id: "90a32f25-4948-45c0-9142-8def637b0a9c",
            business_id: "42132f25-4948-45c0-9142-8def637b0a9c",
            audit_type: "monthly",
            trigger_source: "manual",
            status: "completed",
            thread_id: "audit-thread-1",
            job_id: null,
            period_start: "2026-11-01",
            period_end: "2026-11-30",
            scope_filters: {},
            loaded_data_summary: {},
            severity_summary: {
              critical: 0,
              high: 1,
              medium: 0,
              low: 0,
              total: 1,
              health_status: "attention_needed",
            },
            findings: [
              {
                id: "F-001",
                severity: "High",
                category: "bank_reconciliation",
                rule_violated: "unreviewed_bank_rows",
                affected_records: ["bank_row:1"],
                expected_vs_actual: {
                  expected: "0 unreviewed bank rows",
                  actual: "2 unreviewed bank rows",
                  difference: "2",
                },
                evidence: [
                  "2 imported bank statement rows are waiting for review.",
                ],
                recommendation:
                  "Categorize or match the remaining bank statement rows before closing the month.",
              },
            ],
            raw_report: {},
            polished_report: {
              executive_summary:
                "Checked November 2026 records and found 1 item to review.",
              severity_breakdown: "Critical: 0 | High: 1 | Medium: 0 | Low: 0",
              findings_narrative: [
                {
                  id: "F-001",
                  severity: "High",
                  what: "Two imported bank statement rows are still waiting for review.",
                  where: "Banking feed",
                  why_it_matters:
                    "Unreviewed bank rows can leave cash or expenses understated.",
                  recommended_fix:
                    "Review and match both statement rows in Banking.",
                },
              ],
              next_steps: [
                "Review the two pending bank statement rows in Banking.",
              ],
              markdown: "# Financial Audit",
              provider: "groq",
              model_name: "llama-3.3-70b-versatile",
              fallback_used: false,
              fallback_reason: "",
              numeric_guard_passed: true,
              chunks_processed: 1,
            },
            retrieved_context: [],
            metadata: {},
            created_by: "d1c40361-f7c0-47c2-8df2-c4acb6ebfab6",
            created_at: "2026-11-26T12:00:00Z",
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

    // Verify Settings Business, Team, WhatsApp, and Slack tabs
    for (const section of ["business", "team", "whatsapp", "slack"]) {
      const st = wrap(<Settings initialSection={section} />);
      await waitFor(() =>
        expect(st.container.querySelector("h1")).toBeTruthy(),
      );
      if (section === "business") {
        expect(st.container.textContent).toContain("Accrual");
        expect(st.container.textContent).toContain("January");
      }
      if (section === "team") {
        await waitFor(() =>
          expect(st.container.textContent).toContain("Workspace member"),
        );
        expect(st.container.textContent).not.toContain("d1c40361…");
        expect(st.container.textContent).toContain("Accountant");
      }
      assertNoInternalLabels(
        st.container.textContent || "",
        `Settings:${section}`,
      );
      st.unmount();
    }

    const cleanup = wrap(<CleanupControls />);
    await waitFor(() => expect(cleanup.container.textContent).toBeTruthy());
    assertNoInternalLabels(
      cleanup.container.textContent || "",
      "CleanupControls",
    );
    cleanup.unmount();

    const retention = wrap(<RetentionControls />);
    await waitFor(() =>
      expect(retention.container.textContent).toContain("Trade documents"),
    );
    assertNoInternalLabels(
      retention.container.textContent || "",
      "RetentionControls",
    );
    retention.unmount();

    const closePkg = wrap(<ClosePackages periodId="period-1" />);
    await waitFor(() => expect(closePkg.container.textContent).toBeTruthy());
    assertNoInternalLabels(
      closePkg.container.textContent || "",
      "ClosePackages",
    );
    closePkg.unmount();

    const qb = wrap(<QuickBooksImport />);
    await waitFor(() => expect(qb.container.textContent).toBeTruthy());
    assertNoInternalLabels(qb.container.textContent || "", "QuickBooksImport");
    qb.unmount();

    const finAudit = wrap(<FinancialAudit />);
    await waitFor(() =>
      expect(finAudit.container.textContent).toContain("Checked November 2026"),
    );
    assertNoInternalLabels(
      finAudit.container.textContent || "",
      "FinancialAudit",
    );
    finAudit.unmount();

    getSpy.mockRestore();
  });
});
