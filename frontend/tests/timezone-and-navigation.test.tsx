import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { api } from "../src/api/runtime";
import {
  TimezoneSelect,
  formatTimezoneWithOffset,
  getSupportedTimezoneOptions,
} from "../src/components/timezone-select";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";
import { P3AutomationHub } from "../src/features/p3-automation";

const mockBusiness: Business = {
  id: "42132f25-4948-45c0-9142-8def637b0a9c",
  name: "Acme Books",
  currency: "USD",
  currency_precision: 2,
  timezone: "Asia/Karachi",
  basis: "accrual",
  version: 2,
  policy_version: 1,
  fiscal_month: 1,
  jurisdiction: "PK",
  p2_projects_available: true,
  p2_insights_available: true,
  p2_retention_available: true,
  p2_audit_available: true,
  p2_reconciliation_available: true,
  p2_micro_close_available: true,
  p2_prepaids_available: true,
};

describe("TimezoneSelect IANA dropdown", () => {
  it("lists IANA timezones with UTC offsets, supports filtering, and preserves existing stored values", async () => {
    const options = getSupportedTimezoneOptions("UTC");
    expect(options.length).toBeGreaterThan(20);
    const karachi = options.find((o) => o.value === "Asia/Karachi");
    expect(karachi).toBeDefined();
    expect(karachi?.label).toBe("Asia/Karachi (UTC+05:00)");
    expect(formatTimezoneWithOffset("UTC")).toBe("UTC (UTC+00:00)");

    const user = userEvent.setup();
    render(
      <TimezoneSelect name="timezone" label="Timezone" defaultValue="UTC" />,
    );

    const select = screen.getByLabelText("Timezone") as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(select.value).toBe("UTC");

    const searchBox = screen.getByLabelText("Search timezone");
    await user.type(searchBox, "Karachi");

    expect(select.value).toBe("Asia/Karachi");
    expect(
      screen.getByRole("option", { name: "Asia/Karachi (UTC+05:00)" }),
    ).toBeInTheDocument();
  });
});

describe("Standalone Advanced route scoping (Issue 6)", () => {
  it("omits global cross-app category tabs on standalone Advanced pages while keeping Workspace governance sub-tabs on Usage limits", async () => {
    const getSpy = vi.spyOn(api, "GET").mockImplementation((async (
      url: string,
    ) => {
      if (url.endsWith("/p4/inventory")) {
        return {
          response: new Response(null, { status: 200 }),
          data: { warehouses: [], items: [], movements: [], valuation: [] },
        };
      }
      if (url.endsWith("/p4/operations")) {
        return {
          response: new Response(null, { status: 200 }),
          data: {
            quota: {
              plan_tier: "enterprise",
              daily_doc_limit: 5000,
              daily_docs_used: 12,
              monthly_ai_budget_usd: "150.00",
              monthly_ai_spent_usd: "2.40",
              storage_gb_limit: 100,
              storage_gb_used: "0.4",
              hard_cap_enforced: true,
              manual_workflows_active: true,
            },
            slos: [],
            cost_ledger: [],
          },
        };
      }
      return {
        response: new Response(null, { status: 200 }),
        data: {},
      };
    }) as never);

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const { unmount } = render(
      <QueryClientProvider client={qc}>
        <AccessContext.Provider
          value={{
            user_id: "u1",
            role: "owner",
            active: true,
            can_post: true,
            can_invite: true,
            version: 1,
          }}
        >
          <BusinessContext.Provider value={mockBusiness}>
            <P3AutomationHub initialTab="p4-inventory" standalone />
          </BusinessContext.Provider>
        </AccessContext.Provider>
      </QueryClientProvider>,
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "Inventory" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /Payments & reminders/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /Data migration/i }),
    ).not.toBeInTheDocument();
    unmount();

    render(
      <QueryClientProvider client={qc}>
        <AccessContext.Provider
          value={{
            user_id: "u1",
            role: "owner",
            active: true,
            can_post: true,
            can_invite: true,
            version: 1,
          }}
        >
          <BusinessContext.Provider value={mockBusiness}>
            <P3AutomationHub initialTab="p4-operations" standalone />
          </BusinessContext.Provider>
        </AccessContext.Provider>
      </QueryClientProvider>,
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "Usage limits" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /Payments & reminders/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Data encryption" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "Single sign-on & region" }),
    ).toBeInTheDocument();

    getSpy.mockRestore();
  });
});
