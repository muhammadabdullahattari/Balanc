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
    if (url.endsWith("/p4/operations")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          quotas: {
            service_tier: "enterprise",
            daily_page_quota: 50000,
            monthly_ai_budget_usd: "500.00",
            storage_quota_gb: 250,
            max_concurrent_workers: 16,
            fair_share_weight: 100,
            hard_quota_enforcement: true,
            alert_threshold_percent: 80,
            runbook_owner: "sre-oncall@balanc.enterprise",
            incident_state: "healthy",
          },
          telemetry: {
            pages_ingested_today: 1200,
            page_utilization_percent: 2.4,
            ai_spend_mtd_usd: "14.4000",
            ai_budget_remaining_usd: "485.60",
            ai_budget_utilization_percent: 2.88,
            storage_used_mb: "240.00",
            alert_triggered: false,
            noisy_neighbor_isolation: "ACTIVE_FAIR_SHARE_SCHEDULER",
          },
        },
      } as never;
    }
    if (url.endsWith("/p4/online-migrations")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          live_ledger_invariants: {
            posted_journal_lines_count: 14,
            total_debits: "2500.00",
            total_credits: "2500.00",
            trial_balance_balanced: true,
          },
          migrations: [],
        },
      } as never;
    }
    if (url.endsWith("/p4/bulk-ingestion")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          cell_telemetry: {
            live_lane_status: "ISOLATED_HEALTHY",
            bulk_lane_status: "ACTIVE_FAIR_SHARE_DRAINING",
            target_daily_capacity_pages: 2000000,
            max_supported_backlog_pages: 500000,
            foreground_p95_slo_ms: 120,
            observed_foreground_p95_ms: 42.5,
            total_pages_processed_workspace: 25000,
          },
          batches: [],
        },
      } as never;
    }
    if (url.endsWith("/p4/disaster-recovery")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          topology: {
            pinned_primary_cell: "eu-central-fra-cell-1",
            active_serving_cell: "eu-central-fra-cell-1",
            latest_fencing_token: 1,
            split_brain_fencing: "MONOTONIC_TOKEN_ENFORCED",
            replication_mode: "SYNCHRONOUS_ENCRYPTED_WAL_AND_EVIDENCE",
            target_rto_seconds: 60,
            target_rpo_seconds: 0,
          },
          drills: [],
        },
      } as never;
    }
    if (url.endsWith("/p4/inventory")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          valuation_summary: {
            total_skus: 1,
            total_units_on_hand: "100.00",
            total_subledger_valuation: "1300.00",
            total_cogs_recognized: "0.00",
            subledger_to_gl_tied_out: true,
          },
          items: [
            {
              id: "item-1",
              sku: "SKU-WIDGET-PRO",
              name: "Precision Sensor Module Pro",
              warehouse_location: "MAIN-WH-01",
              costing_method: "fifo",
              currency: "USD",
              quantity_on_hand: "100.00",
              unit_cost: "13.0000",
              total_valuation: "1300.00",
              fifo_layers: [{ qty: "100.0000", unit_cost: "13.000000" }],
              reorder_point: "20.00",
              low_stock: false,
            },
          ],
          movements: [],
        },
      } as never;
    }
    if (url.endsWith("/p4/group-consolidation")) {
      return {
        response: new Response(null, { status: 200 }),
        data: {
          entities: [],
          runs: [],
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

    await screen.findByRole("heading", { name: "Connect WhatsApp" });
    await screen.findByRole("heading", { name: "Connect Slack" });
    expect(
      screen.getByRole("button", {
        name: "Save WhatsApp connection",
      }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", {
        name: "Save Slack connection",
      }),
    ).toBeEnabled();

    fireEvent.click(screen.getByRole("tab", { name: "Supplier payments" }));
    const stepUpInput = await screen.findByLabelText(
      /Approval code \(from your authenticator app\)/,
    );
    expect((stepUpInput as HTMLInputElement).value).toBe("");

    fireEvent.click(
      await screen.findByRole("button", { name: "Approve and pay" }),
    );
    expect(
      await screen.findByRole("dialog", {
        name: "Approve supplier payment",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve payment" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("tab", { name: "Daily reconciliation" }));
    expect(
      await screen.findByRole("heading", {
        name: "Daily reconciliation",
        level: 2,
      }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: "Bank feeds & documents" }),
    );
    expect(
      await screen.findByRole("heading", {
        name: /Bank feed & merchant payouts/,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Voice notes" }));
    expect(
      await screen.findByRole("heading", { name: "Voice notes", level: 2 }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Document scanning" }));
    expect(
      await screen.findByRole("heading", { name: /Document scanning quality/ }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Revenue & payroll" }));
    expect(
      await screen.findByRole("heading", {
        name: /Revenue schedules, payroll & short payments/,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Inventory & group" }));
    expect(
      await screen.findByRole("heading", {
        name: /Inventory & stock valuation/,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Group reporting" }));
    expect(
      await screen.findByRole("heading", { name: "Group reporting", level: 2 }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Bulk import" }));
    expect(
      await screen.findByRole("heading", {
        name: "Bulk document import",
        level: 2,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Data migration" }));
    expect(
      await screen.findByRole("heading", {
        name: /Data migration & balance check/,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Security & workspace" }));
    expect(
      await screen.findByRole("heading", {
        name: /Usage limits & spending alerts/,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Disaster recovery" }));
    expect(
      await screen.findByRole("heading", {
        name: /Backup & disaster recovery/,
      }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Data encryption" }));
    expect(
      await screen.findByRole("heading", { name: "Data encryption", level: 2 }),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: "Single sign-on & region" }),
    );
    expect(
      await screen.findByRole("heading", {
        name: /Single sign-on & data region/,
      }),
    ).toBeInTheDocument();
  } finally {
    client.clear();
    get.mockRestore();
  }
});
