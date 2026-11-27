import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { api } from "../src/api/runtime";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";
import { Reports } from "../src/features/reports";
import { FinancialAudit } from "../src/features/financial-audit";

const workspace: Business = {
  id: "42132f25-4948-45c0-9142-8def637b0a9c",
  name: "Meridian Studio",
  currency: "USD",
  currency_precision: 2,
  timezone: "UTC",
  basis: "accrual",
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

const clients: QueryClient[] = [];

function mount(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
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
          <BusinessContext.Provider value={workspace}>
            {node}
          </BusinessContext.Provider>
        </AccessContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  clients.forEach((c) => c.clear());
  clients.length = 0;
});

function ok(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) };
}

describe("Grafana & visual financial / audit reports", () => {
  it("renders Profit & Loss KPIs, charts, live Grafana toggle, and supporting table together", async () => {
    vi.spyOn(api, "GET").mockImplementation(((url: string) => {
      if (url.endsWith("/reports/profit-loss")) {
        return Promise.resolve(
          ok({
            report_id: "pl-001",
            timezone: "UTC",
            start_date: "2026-01-01",
            as_of: "2026-11-27",
            net_profit: "7400.00",
            items: [
              {
                account_id: "inc-1",
                code: "4000",
                name: "Advisory revenue",
                kind: "income",
                amount: "10200.00",
              },
              {
                account_id: "exp-1",
                code: "5100",
                name: "Cloud infrastructure",
                kind: "expense",
                amount: "2800.00",
              },
            ],
          }),
        );
      }
      if (url.endsWith("/reports/visuals")) {
        return Promise.resolve(
          ok({
            has_data: true,
            currency: "USD",
            kpis: {
              total_income: "10200.00",
              total_expense: "2800.00",
              net_balance: "7400.00",
              cash_balance: "14500.00",
            },
            income_expense_series: [
              {
                period_label: "2026-10",
                income: "4200.00",
                expense: "1200.00",
                net_profit: "3000.00",
                running_cash: "10100.00",
              },
              {
                period_label: "2026-11",
                income: "6000.00",
                expense: "1600.00",
                net_profit: "4400.00",
                running_cash: "14500.00",
              },
            ],
            expense_categories: [
              {
                label: "Cloud infrastructure",
                amount: "2800.00",
                share_pct: "100.0",
              },
            ],
            grafana: {
              configured: true,
              reachable: true,
              embed_enabled: true,
              status_message:
                "Connected to provisioned Grafana reporting service.",
              embed_urls: {
                "5": "https://grafana.example.test/d-solo/lp-financial-reports/ledgerpilot-financial-reports?panelId=5",
              },
            },
          }),
        );
      }
      if (url.endsWith("/reports/balance-sheet")) {
        return Promise.resolve(
          ok({
            report_id: "bs-001",
            timezone: "UTC",
            as_of: "2026-11-27",
            retained_earnings: "7400.00",
            items: [
              {
                account_id: "a-1",
                code: "1000",
                name: "Operating cash",
                kind: "asset",
                amount: "14500.00",
              },
              {
                account_id: "l-1",
                code: "2000",
                name: "Accounts payable",
                kind: "liability",
                amount: "2100.00",
              },
              {
                account_id: "e-1",
                code: "3000",
                name: "Owner capital",
                kind: "equity",
                amount: "5000.00",
              },
            ],
          }),
        );
      }
      return Promise.resolve(ok({ items: [], next_cursor: null }));
    }) as never);

    mount(<Reports />);

    expect(
      await screen.findByRole("region", { name: "Headline figures" }),
    ).toBeVisible();
    expect(screen.getByText("Grafana connected")).toBeVisible();
    expect(
      screen.getByRole("img", { name: /Income vs\. expenses over time/i }),
    ).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Category spending breakdown" }),
    ).toBeVisible();
    // Supporting table remains verifiable alongside visuals
    expect(screen.getByText("Advisory revenue")).toBeVisible();

    // Toggle live Grafana iframe panel
    await userEvent.click(
      screen.getByRole("button", { name: "Show live Grafana panels" }),
    );
    expect(screen.getByTitle("Income vs. expenses over time")).toHaveAttribute(
      "src",
      expect.stringContaining("/d-solo/lp-financial-reports/"),
    );

    // Switch to Balance Sheet tab and verify Assets = Liabilities + Equity visual
    await userEvent.click(screen.getByRole("tab", { name: "Balance sheet" }));
    expect(
      await screen.findByText("Assets = Liabilities + Equity"),
    ).toBeVisible();
    expect(screen.getByText("Balanced")).toBeVisible();
    expect(screen.getByText("Operating cash")).toBeVisible();
  });

  it("degrades gracefully to supporting table and derived charts when /reports/visuals fails, and shows empty state when no data exists", async () => {
    vi.spyOn(api, "GET").mockImplementation(((url: string) => {
      if (url.endsWith("/reports/visuals")) {
        return Promise.resolve({
          error: { error: { message: "Grafana backend timeout" } },
          response: new Response(null, { status: 503 }),
        });
      }
      if (url.endsWith("/reports/profit-loss")) {
        return Promise.resolve(
          ok({
            report_id: "pl-empty",
            timezone: "UTC",
            start_date: "2026-11-01",
            as_of: "2026-11-30",
            net_profit: "0.00",
            items: [],
          }),
        );
      }
      return Promise.resolve(ok({ items: [], next_cursor: null }));
    }) as never);

    mount(<Reports />);

    expect(await screen.findByText("Net profit")).toBeVisible();
    expect(
      screen.getAllByText("No data for this period").length,
    ).toBeGreaterThan(0);
    // Must not block the report with a fatal top-level alert when visuals endpoint returns 503
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders Financial Audit severity, category, and historical trend charts alongside findings", async () => {
    vi.spyOn(api, "GET").mockImplementation(((url: string) => {
      if (url === "/api/v1/businesses/{business_id}/audits") {
        return Promise.resolve(
          ok({
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
                  critical: 1,
                  high: 1,
                  medium: 0,
                  low: 0,
                  total: 2,
                  health_status: "critical_attention_required",
                },
                executive_summary: "Found 2 items requiring attention.",
                created_at: "2026-11-27T10:00:00Z",
              },
            ],
            next_cursor: null,
          }),
        );
      }
      if (url === "/api/v1/businesses/{business_id}/audits/{audit_id}") {
        return Promise.resolve(
          ok({
            id: "90a32f25-4948-45c0-9142-8def637b0a9c",
            business_id: workspace.id,
            audit_type: "monthly",
            trigger_source: "manual",
            status: "completed",
            thread_id: "audit-1",
            job_id: null,
            period_start: "2026-11-01",
            period_end: "2026-11-30",
            scope_filters: {},
            loaded_data_summary: {},
            severity_summary: {
              critical: 1,
              high: 1,
              medium: 0,
              low: 0,
              total: 2,
              health_status: "critical_attention_required",
            },
            findings: [
              {
                id: "F-1",
                severity: "Critical",
                category: "trial_balance",
                rule_violated: "balance_continuity",
                affected_records: [],
                expected_vs_actual: {
                  expected: "Balanced",
                  actual: "Unreconciled bank item",
                },
                evidence: ["Statement difference detected"],
                recommendation: "Match pending statement row.",
              },
            ],
            raw_report: {},
            polished_report: {
              executive_summary: "Found 2 items requiring attention.",
              severity_breakdown: "Critical: 1 | High: 1",
              findings_narrative: [],
              next_steps: ["Match pending statement row."],
              markdown: "# Audit",
              provider: "deterministic",
              model_name: "deterministic",
              fallback_used: true,
              fallback_reason: "",
              numeric_guard_passed: true,
              chunks_processed: 1,
            },
            retrieved_context: [],
            metadata: {},
            created_by: "d1c40361-f7c0-47c2-8df2-c4acb6ebfab6",
            created_at: "2026-11-27T10:00:00Z",
          }),
        );
      }
      if (url === "/api/v1/businesses/{business_id}/audits/visuals") {
        return Promise.resolve(
          ok({
            has_data: true,
            by_severity: [
              { severity: "Critical", count: 1 },
              { severity: "High", count: 1 },
              { severity: "Medium", count: 0 },
              { severity: "Low", count: 0 },
            ],
            by_category: [{ category: "Trial Balance", count: 2 }],
            issues_over_time: [
              {
                period_label: "2026-11-01 → 2026-11-30",
                audit_type: "monthly",
                total: 2,
                critical: 1,
              },
            ],
            grafana: {
              configured: false,
              reachable: false,
              embed_enabled: false,
              status_message:
                "Grafana is not configured — displaying verified SQL charts.",
              embed_urls: {},
            },
          }),
        );
      }
      return Promise.resolve(
        ok({ mermaid: "graph TD;", nodes: [], tools: [] }),
      );
    }) as never);

    mount(<FinancialAudit />);

    expect(
      await screen.findByRole("img", { name: "Audit issues by severity" }),
    ).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Issues by category" }),
    ).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Audit issues over time" }),
    ).toBeVisible();
    expect(await screen.findByText("Server-verified charts")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Download PDF with charts" }),
    ).toBeVisible();
  });
});
