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
          beneficiaries: [
            {
              id: "ben-1",
              supplier_name: "Amazon Web Services Inc.",
              account_holder_name: "AWS Treasury",
              bank_name: "JPMorgan Chase",
              routing_or_swift: "021000021",
              account_number_masked: "********4321",
              currency: "USD",
              rail: "stripe_live",
              out_of_band_verified: true,
              verification_reference: "Callback verified",
              version: 1,
            },
          ],
          payments: [
            {
              id: "pay-1",
              bill_reference: "BILL-AWS-2026-114",
              supplier_name: "Amazon Web Services Inc.",
              beneficiary_id: "ben-1",
              beneficiary_version: 1,
              amount: "425.00",
              currency: "USD",
              rail: "stripe_live",
              state: "prepared",
              requires_dual_approval: false,
              step_up_verified: false,
              provider_reference: "",
              remittance_advice: "",
              failure_reason: "",
              created_at: "2026-11-25T00:00:00Z",
              executed_at: null,
            },
          ],
        },
      } as never;
    }
    if (url.endsWith("/p3/reconciliation-sla")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          tracker: {
            id: "sla-1",
            target_date: "2026-11-25",
            sla_hours: 24,
            started_at: "2026-11-25T00:00:00Z",
            deadline_at: "2026-11-26T00:00:00Z",
            completed_at: null,
            status: "on_track",
            remaining_seconds: 72000,
            completion_percent: 75,
            pending_bank_rows: 1,
            missing_evidence_bills: 0,
            pending_drafts: 0,
            pending_close_tasks: 0,
            total_blockers: 1,
            chased_count: 2,
            last_chased_at: null,
            checklist: [
              {
                key: "bank_feed_reconciled",
                label: "Bank feed rows reviewed & reconciled",
                open_count: 1,
                completed: false,
              },
            ],
            unlinked_bills: [],
            unreviewed_bank_rows: [],
          },
        },
      } as never;
    }
    if (url.endsWith("/p3/payout-reconciliation")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          feed_status: {
            stripe_financial_connections_live: true,
            provider_mode: "stripe_live_feed",
          },
          recent_bank_feed_rows: [],
          reconciliations: [],
        },
      } as never;
    }
    if (url.endsWith("/p3/voice-instructions")) {
      return {
        response: new Response(null, { status: 200 }),
        data: { instructions: [] },
      } as never;
    }
    if (url.endsWith("/p3/encryption-keys")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          envelope_status: {
            master_kek_configured: true,
            active_key_version: 1,
            active_key_status: "active",
            protected_records_count: 3,
            provably_shredded: false,
          },
          keys: [],
        },
      } as never;
    }
    if (url.endsWith("/p3/ai-benchmarks")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          summary: {
            baseline_p2_cost_per_doc_usd: "0.0420",
            current_p3_cost_per_doc_usd: "0.0145",
            cost_reduction_percent: 65.48,
            target_reduction_percent: 50.0,
            target_met: true,
          },
          runs: [],
        },
      } as never;
    }
    if (url.endsWith("/p3/accounting-expansion")) {
      return {
        response: new Response(null, { status: 200 }),
        data: { workflows: [] },
      } as never;
    }
    if (url.endsWith("/p3/enterprise-sso-cell")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          config: {
            protocol: "oidc",
            idp_provider: "okta",
            issuer_url: "https://acme.okta.com/oauth2/default",
            sso_client_id: "0oa1balancenterprise",
            sso_secret_configured: true,
            email_domain: "acme.example",
            enforce_sso: true,
            jit_default_role: "reviewer",
            region_cell: "eu-central-fra-cell-1",
            data_residency_locked: true,
            cross_region_egress_blocked: true,
            residency_attestation_sha256:
              "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            last_verified_at: null,
          },
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

    fireEvent.click(
      await screen.findByRole("button", { name: "Approve & Release Payment" }),
    );
    expect(
      await screen.findByRole("dialog", {
        name: "Confirm Supplier Payment Settlement",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Confirm & Execute Settlement" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(
      screen.getByRole("tab", { name: /3\.1 · 1-Day Reconciliation SLA/ }),
    );
    expect(
      await screen.findByText(/One-Day Reconciliation SLA Tracker/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: /3\.2 · Bank Feed & Gross-to-Net/ }),
    );
    expect(
      await screen.findByText(/Reconcile Gross-to-Net Merchant Payout Split/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: /3\.3 · Voice-Note Accounting/ }),
    );
    expect(
      await screen.findByText(/Voice-Note Accounting Instruction Review/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: /3\.4 · Envelope Encryption & Shred/ }),
    );
    expect(
      await screen.findByText(/Per-Tenant Envelope Encryption Keys/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: /P3-F · Multilingual OCR & AI Cost/ }),
    );
    expect(
      await screen.findByText(/50% AI Cost-Reduction Benchmark/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", {
        name: /P3-I · Revenue, Payroll & Short-Pay/,
      }),
    );
    expect(
      await screen.findByText(/Contract Revenue Schedules/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: /P3-K · Enterprise SSO & Cell/ }),
    );
    expect(
      await screen.findByText(/Regional Data-Residency Cell Isolation/),
    ).toBeInTheDocument();
  } finally {
    client.clear();
    get.mockRestore();
  }
});
