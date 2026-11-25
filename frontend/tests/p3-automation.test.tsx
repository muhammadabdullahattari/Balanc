import { expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { P3AutomationHub } from "../src/features/p3-automation";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";
import { api } from "../src/api/runtime";

it("renders P3 automation tabs, workspace channel connector, and empty step-up 2FA input", async () => {
  const get = vi.spyOn(api, "GET").mockImplementation(async (url: string) => {
    if (url.endsWith("/p3/reminders")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          channels_status: {
            whatsapp_cloud_connected: true,
            whatsapp_mode: "workspace_custom",
            whatsapp_phone_number_id: "1391722684032246",
            default_whatsapp_recipient: "+923412685261",
            slack_connected: true,
            slack_mode: "workspace_custom",
            slack_workspace_name: "Acme Finance",
            default_slack_channel: "#all-balanc",
            email_fallback_enabled: true,
          },
          preferences: [],
          reminders: [],
        },
      } as never;
    }
    if (url.endsWith("/p3/supplier-payments")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          controls: {
            kill_switch_active: false,
            kill_switch_reason: "",
            single_payment_limit: "50000.00",
            dual_approval_threshold: "5000.00",
            default_rail: "stripe_live",
            stripe_live_available: true,
          },
          beneficiaries: [],
          payments: [],
        },
      } as never;
    }
    return {
      response: new Response(null, { status: 200 }),
      data: { connections: [], ingestions: [] },
    } as never;
  });

  const client = new QueryClient();
  try {
    render(
      <QueryClientProvider client={client}>
        <AccessContext.Provider
          value={{ role: "owner", can_post: true, can_invite: true }}
        >
          <BusinessContext.Provider
            value={{ id: "biz-1", currency: "USD" } as Business}
          >
            <P3AutomationHub initialTab="reminders" />
          </BusinessContext.Provider>
        </AccessContext.Provider>
      </QueryClientProvider>,
    );

    await screen.findByText(/Connect Your Workspace WhatsApp & Slack Accounts/);
    expect(
      screen.getByRole("button", {
        name: "Save Workspace WhatsApp & Slack Connection",
      }),
    ).toBeEnabled();

    fireEvent.click(
      screen.getByRole("tab", { name: /Human-Approved Supplier Payments/ }),
    );
    const stepUpInput = await screen.findByLabelText(/Step-Up 2FA Code/);
    expect((stepUpInput as HTMLInputElement).value).toBe("");
  } finally {
    client.clear();
    get.mockRestore();
  }
});
