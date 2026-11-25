import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Modal,
  PageHeading,
  SectionTabs,
  Table,
} from "../components/ui";
import { useAccess, useBusiness } from "./context";

interface RemindersData {
  channels_status: {
    whatsapp_cloud_connected: boolean;
    whatsapp_mode?: "workspace_custom" | "platform_default" | "sandbox";
    whatsapp_phone_number_id: string;
    whatsapp_business_account_id?: string;
    whatsapp_token_masked?: string;
    default_whatsapp_recipient: string;
    slack_connected: boolean;
    slack_mode?: "workspace_custom" | "platform_default" | "sandbox";
    slack_workspace_name?: string;
    slack_credentials_masked?: string;
    default_slack_channel: string;
    email_fallback_enabled: boolean;
  };
  preferences: Array<{
    id: string;
    recipient_key: string;
    recipient_name: string;
    whatsapp_phone: string;
    slack_channel: string;
    email_address: string;
    whatsapp_opt_in: boolean;
    slack_opt_in: boolean;
    email_fallback: boolean;
    channel_order: string[];
    quiet_start_hour: number;
    quiet_end_hour: number;
    timezone: string;
    max_attempts: number;
    opted_out: boolean;
  }>;
  reminders: Array<{
    id: string;
    target_kind: string;
    target_id: string;
    target_label: string;
    recipient_key: string;
    channel: string;
    destination: string;
    message_body: string;
    attempt_number: number;
    status: string;
    provider_message_id: string;
    created_at: string;
  }>;
}

interface PortalsData {
  connections: Array<{
    id: string;
    vendor_name: string;
    portal_kind: string;
    portal_url: string;
    login_username: string;
    totp_enabled: boolean;
    totp_seed_preview: string;
    live_totp_preview: string;
    schedule_frequency: string;
    status: string;
    last_sync_at: string | null;
    last_totp_verified_at: string | null;
  }>;
  ingestions: Array<{
    id: string;
    connection_id: string;
    external_invoice_id: string;
    invoice_number: string;
    vendor_name: string;
    invoice_date: string;
    due_date: string;
    currency: string;
    total_amount: string;
    tax_amount: string;
    content_sha256: string;
    trade_draft_id: string | null;
    totp_code_used_hash: string;
    ocr_confidence: number;
    status: string;
    created_at: string;
  }>;
}

interface PaymentsData {
  controls: {
    kill_switch_active: boolean;
    kill_switch_reason: string;
    single_payment_limit: string;
    dual_approval_threshold: string;
    default_rail: "stripe_live" | "ach_nacha" | "swift_wire";
    stripe_live_available: boolean;
  };
  beneficiaries: Array<{
    id: string;
    supplier_name: string;
    account_holder_name: string;
    bank_name: string;
    routing_or_swift: string;
    account_number_masked: string;
    currency: string;
    rail: string;
    out_of_band_verified: boolean;
    verification_reference: string;
    version: number;
  }>;
  payments: Array<{
    id: string;
    bill_reference: string;
    supplier_name: string;
    beneficiary_id: string;
    beneficiary_version: number;
    amount: string;
    currency: string;
    rail: string;
    state: string;
    requires_dual_approval: boolean;
    step_up_verified: boolean;
    provider_reference: string;
    remittance_advice: string;
    settlement_journal_id?: string | null;
    first_approved_by?: string | null;
    final_approved_by?: string | null;
    failure_reason: string;
    created_at: string;
    executed_at: string | null;
  }>;
}

export function MultichannelReminders() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [targetKind, setTargetKind] = useState<
    "bill" | "invoice" | "evidence" | "approval"
  >("bill");
  const [targetLabel, setTargetLabel] = useState(
    "AWS Cloud Hosting Bill #INV-2026-114 ($800.00 - Due in 2 days)",
  );
  const [recipientKey, setRecipientKey] = useState("ap-finance-owner");
  const [channel, setChannel] = useState<
    "auto" | "whatsapp" | "slack" | "email"
  >("whatsapp");
  const [customMessage, setCustomMessage] = useState(
    "LedgerPilot Reminder: AWS Cloud Hosting Bill #INV-2026-114 for $800.00 is awaiting approval & supporting receipt.",
  );
  const [lastResult, setLastResult] = useState<string>("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-reminders"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/reminders", {
          signal,
          params: { path },
        }),
      )) as unknown as RemindersData,
  });

  const dispatchMutation = useMutation({
    mutationFn: async (
      overrideChannel?: "auto" | "whatsapp" | "slack" | "email",
    ) =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p3/reminders/dispatch", {
          params: { path },
          body: {
            target_kind: targetKind,
            target_label: targetLabel,
            recipient_key: recipientKey,
            channel: overrideChannel || channel,
            template_name: "hello_world",
            custom_message: customMessage,
            idempotency_key: "",
            is_resolved_override: false,
          },
        }),
      )) as unknown as {
        id: string;
        status: string;
        channel: string;
        destination: string;
        provider_message_id: string;
      },
    onSuccess: (res) => {
      setLastResult(
        `Dispatched via ${res.channel.toUpperCase()} to ${res.destination} — Status: ${res.status} (${res.provider_message_id || "recorded"})`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reminders"],
      });
    },
  });

  const prefMutation = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const fd = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/p3/reminders/preferences", {
          params: { path },
          body: {
            recipient_key: String(
              fd.get("recipient_key") || "ap-finance-owner",
            ),
            recipient_name: String(
              fd.get("recipient_name") || "Finance Approver",
            ),
            whatsapp_phone: String(fd.get("whatsapp_phone") || "+923412685261"),
            slack_channel: String(fd.get("slack_channel") || "#all-balanc"),
            email_address: String(fd.get("email_address") || ""),
            whatsapp_opt_in: fd.get("whatsapp_opt_in") === "on",
            slack_opt_in: fd.get("slack_opt_in") === "on",
            email_fallback: true,
            quiet_start_hour: Number(fd.get("quiet_start_hour") || 22),
            quiet_end_hour: Number(fd.get("quiet_end_hour") || 7),
            timezone: "Asia/Karachi",
            locale: "en_US",
            max_attempts: Number(fd.get("max_attempts") || 3),
            opted_out: fd.get("opted_out") === "on",
          },
        }),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reminders"],
      });
    },
  });

  const cs = query.data?.channels_status;
  const [channelStatusNotice, setChannelStatusNotice] = useState<string>("");

  const webhookStatusMutation = useMutation({
    mutationFn: (payload: {
      reminderId: string;
      event_source: "whatsapp_cloud_webhook" | "slack_interactive_webhook";
      delivery_status: "delivered" | "read" | "failed";
      interactive_action:
        "none" | "acknowledge" | "evidence_uploaded" | "opt_out";
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/reminders/{reminder_id}/webhook-status",
          {
            params: {
              path: { business_id: b.id, reminder_id: payload.reminderId },
            },
            body: {
              event_source: payload.event_source,
              delivery_status: payload.delivery_status,
              interactive_action: payload.interactive_action,
              actor_handle: "",
            },
          },
        ),
      ),
    onSuccess: (res) => {
      const r = res as { status: string; interactive_action: string };
      setLastResult(
        `Webhook callback processed -> Status updated to ${r.status.toUpperCase()} (Action: ${r.interactive_action})`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reminders"],
      });
    },
  });

  const connectChannelsMutation = useMutation({
    mutationFn: (payload: {
      whatsapp_phone_number_id?: string;
      whatsapp_business_account_id?: string;
      whatsapp_access_token?: string;
      default_whatsapp_recipient?: string;
      slack_workspace_name?: string;
      slack_webhook_url?: string;
      slack_bot_token?: string;
      default_slack_channel?: string;
      disconnect_whatsapp?: boolean;
      disconnect_slack?: boolean;
    }) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/p3/reminders/channels", {
          params: { path },
          body: {
            whatsapp_phone_number_id: payload.whatsapp_phone_number_id || "",
            whatsapp_business_account_id:
              payload.whatsapp_business_account_id || "",
            whatsapp_access_token: payload.whatsapp_access_token || "",
            default_whatsapp_recipient:
              payload.default_whatsapp_recipient || "",
            slack_workspace_name: payload.slack_workspace_name || "",
            slack_webhook_url: payload.slack_webhook_url || "",
            slack_bot_token: payload.slack_bot_token || "",
            default_slack_channel: payload.default_slack_channel || "",
            disconnect_whatsapp: Boolean(payload.disconnect_whatsapp),
            disconnect_slack: Boolean(payload.disconnect_slack),
          },
        }),
      ),
    onSuccess: () => {
      setChannelStatusNotice(
        "Workspace WhatsApp & Slack integration credentials saved and encrypted for this business.",
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reminders"],
      });
    },
  });

  return (
    <div className="stack">
      <div className="panel">
        <h2>P3 Feature C · Multichannel WhatsApp & Slack Reminders</h2>
        <p className="muted">
          Consent-based evidence chasing, overdue bill alerts, and approval
          reminders across Official Meta WhatsApp Cloud API and Slack with
          automatic resolution recheck, quiet hours, attempt caps, and email
          fallback.
        </p>
        <div
          style={{
            display: "flex",
            gap: "12px",
            flexWrap: "wrap",
            marginTop: "12px",
          }}
        >
          <Badge>
            WhatsApp Cloud API:{" "}
            {cs?.whatsapp_mode === "workspace_custom"
              ? "WORKSPACE CONNECTED"
              : cs?.whatsapp_cloud_connected
                ? "CONNECTED"
                : "SANDBOX READY"}{" "}
            ({cs?.default_whatsapp_recipient || "+923412685261"})
          </Badge>
          <Badge>
            Slack Workspace:{" "}
            {cs?.slack_mode === "workspace_custom"
              ? `WORKSPACE CONNECTED (${cs.slack_workspace_name || "Custom"})`
              : cs?.slack_connected
                ? "CONNECTED"
                : "SANDBOX READY"}{" "}
            ({cs?.default_slack_channel || "#all-balanc"})
          </Badge>
          <Badge>Email Fallback: ACTIVE</Badge>
        </div>
      </div>

      <div className="panel">
        <h3>0. Connect Your Workspace WhatsApp & Slack Accounts</h3>
        <p className="muted">
          Connect your own business Meta WhatsApp Cloud API sender and Slack
          Workspace (encrypted per business workspace), or leave blank to use
          the default connected platform channels.
        </p>
        <ErrorNotice error={connectChannelsMutation.error} />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            connectChannelsMutation.mutate({
              whatsapp_phone_number_id: String(
                fd.get("whatsapp_phone_number_id") || "",
              ),
              whatsapp_business_account_id: String(
                fd.get("whatsapp_business_account_id") || "",
              ),
              whatsapp_access_token: String(
                fd.get("whatsapp_access_token") || "",
              ),
              default_whatsapp_recipient: String(
                fd.get("default_whatsapp_recipient") || "",
              ),
              slack_workspace_name: String(
                fd.get("slack_workspace_name") || "",
              ),
              slack_webhook_url: String(fd.get("slack_webhook_url") || ""),
              slack_bot_token: String(fd.get("slack_bot_token") || ""),
              default_slack_channel: String(
                fd.get("default_slack_channel") || "",
              ),
            });
          }}
        >
          <div className="form-grid">
            <Field label="WhatsApp Phone Number ID">
              <input
                name="whatsapp_phone_number_id"
                placeholder={
                  cs?.whatsapp_phone_number_id || "e.g. 1391722684032246"
                }
              />
            </Field>
            <Field label="WhatsApp Business Account ID (WABA)">
              <input
                name="whatsapp_business_account_id"
                placeholder={
                  cs?.whatsapp_business_account_id || "e.g. 1877048859962524"
                }
              />
            </Field>
            <Field
              label={`Meta Cloud API Access Token ${cs?.whatsapp_token_masked ? `(Saved: ${cs.whatsapp_token_masked})` : ""}`}
            >
              <input
                name="whatsapp_access_token"
                type="password"
                placeholder="EAAa... (Fernet-encrypted at rest)"
              />
            </Field>
            <Field label="Default WhatsApp Recipient (E.164)">
              <input
                name="default_whatsapp_recipient"
                defaultValue={cs?.default_whatsapp_recipient || "+923412685261"}
                placeholder="+923412685261"
              />
            </Field>
            <Field label="Slack Workspace Name">
              <input
                name="slack_workspace_name"
                defaultValue={cs?.slack_workspace_name || ""}
                placeholder="e.g. Acme Finance Workspace"
              />
            </Field>
            <Field
              label={`Slack Incoming Webhook URL ${cs?.slack_credentials_masked ? `(Saved: ${cs.slack_credentials_masked})` : ""}`}
            >
              <input
                name="slack_webhook_url"
                type="password"
                placeholder="https://hooks.slack.com/services/T.../B.../..."
              />
            </Field>
            <Field label="Slack Bot OAuth Token (Optional)">
              <input
                name="slack_bot_token"
                type="password"
                placeholder="xoxb-... (Optional if Webhook URL is provided)"
              />
            </Field>
            <Field label="Default Slack Channel">
              <input
                name="default_slack_channel"
                defaultValue={cs?.default_slack_channel || "#all-balanc"}
                placeholder="#finance-alerts"
              />
            </Field>
          </div>
          <div
            style={{
              display: "flex",
              gap: "10px",
              flexWrap: "wrap",
              marginTop: "10px",
            }}
          >
            <Button type="submit" busy={connectChannelsMutation.isPending}>
              Save Workspace WhatsApp & Slack Connection
            </Button>
            {cs?.whatsapp_mode === "workspace_custom" && (
              <Button
                type="button"
                variant="secondary"
                busy={connectChannelsMutation.isPending}
                onClick={() =>
                  connectChannelsMutation.mutate({ disconnect_whatsapp: true })
                }
              >
                Disconnect Custom WhatsApp
              </Button>
            )}
            {cs?.slack_mode === "workspace_custom" && (
              <Button
                type="button"
                variant="secondary"
                busy={connectChannelsMutation.isPending}
                onClick={() =>
                  connectChannelsMutation.mutate({ disconnect_slack: true })
                }
              >
                Disconnect Custom Slack
              </Button>
            )}
          </div>
          {channelStatusNotice && (
            <p
              role="status"
              className="success-text"
              style={{ marginTop: "8px" }}
            >
              {channelStatusNotice}
            </p>
          )}
        </form>
      </div>

      <div className="panel">
        <h3>1. Send Live WhatsApp or Slack Reminder</h3>
        <ErrorNotice error={dispatchMutation.error} />
        <div className="form-grid">
          <Field label="Reminder Target Type">
            <select
              value={targetKind}
              onChange={(e) =>
                setTargetKind(
                  e.target.value as
                    "bill" | "invoice" | "evidence" | "approval",
                )
              }
            >
              <option value="bill">Overdue Supplier Bill</option>
              <option value="evidence">Missing Receipt / Document</option>
              <option value="approval">Pending Payment Approval</option>
              <option value="invoice">Customer Invoice Follow-Up</option>
            </select>
          </Field>
          <Field label="Recipient Key">
            <input
              value={recipientKey}
              onChange={(e) => setRecipientKey(e.target.value)}
            />
          </Field>
          <Field label="Target Description / Bill Reference">
            <input
              value={targetLabel}
              onChange={(e) => setTargetLabel(e.target.value)}
            />
          </Field>
          <Field label="Delivery Channel">
            <select
              value={channel}
              onChange={(e) =>
                setChannel(
                  e.target.value as "auto" | "whatsapp" | "slack" | "email",
                )
              }
            >
              <option value="whatsapp">
                WhatsApp Cloud API (
                {cs?.default_whatsapp_recipient || "+923412685261"})
              </option>
              <option value="slack">
                Slack Channel ({cs?.default_slack_channel || "#all-balanc"})
              </option>
              <option value="email">Email Fallback</option>
              <option value="auto">Auto (Recipient Preference Order)</option>
            </select>
          </Field>
        </div>
        <Field label="Message Body">
          <textarea
            rows={2}
            value={customMessage}
            onChange={(e) => setCustomMessage(e.target.value)}
          />
        </Field>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <Button
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate("whatsapp")}
          >
            Send Live WhatsApp Reminder
          </Button>
          <Button
            variant="secondary"
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate("slack")}
          >
            Send Live Slack Reminder (#all-balanc)
          </Button>
          <Button
            variant="secondary"
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate(undefined)}
          >
            Dispatch Selected Channel
          </Button>
        </div>
        {lastResult && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "10px" }}
          >
            {lastResult}
          </p>
        )}
      </div>

      <div className="panel">
        <h3>2. Recipient Consent, Quiet Hours & Attempt Caps</h3>
        <ErrorNotice error={prefMutation.error} />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            prefMutation.mutate(e.currentTarget);
          }}
        >
          <div className="form-grid">
            <Field label="Recipient Key">
              <input
                name="recipient_key"
                defaultValue="ap-finance-owner"
                required
              />
            </Field>
            <Field label="Recipient Name">
              <input
                name="recipient_name"
                defaultValue="Muhammad Abdullah (Finance)"
                required
              />
            </Field>
            <Field label="WhatsApp Phone (E.164)">
              <input
                name="whatsapp_phone"
                defaultValue={cs?.default_whatsapp_recipient || "+923412685261"}
              />
            </Field>
            <Field label="Slack Channel">
              <input
                name="slack_channel"
                defaultValue={cs?.default_slack_channel || "#all-balanc"}
              />
            </Field>
            <Field label="Quiet Hours Start (0-23)">
              <input
                name="quiet_start_hour"
                type="number"
                min={0}
                max={23}
                defaultValue={22}
              />
            </Field>
            <Field label="Quiet Hours End (0-23)">
              <input
                name="quiet_end_hour"
                type="number"
                min={0}
                max={23}
                defaultValue={7}
              />
            </Field>
            <Field label="Max Reminder Attempts">
              <input
                name="max_attempts"
                type="number"
                min={1}
                max={10}
                defaultValue={3}
              />
            </Field>
          </div>
          <div
            style={{
              display: "flex",
              gap: "18px",
              flexWrap: "wrap",
              margin: "10px 0",
            }}
          >
            <label>
              <input name="whatsapp_opt_in" type="checkbox" defaultChecked />{" "}
              WhatsApp Consent Opt-In
            </label>
            <label>
              <input name="slack_opt_in" type="checkbox" defaultChecked /> Slack
              Consent Opt-In
            </label>
            <label>
              <input name="opted_out" type="checkbox" /> Opted Out (Block All
              Chasing)
            </label>
          </div>
          <Button busy={prefMutation.isPending}>
            Save Recipient Preferences
          </Button>
        </form>
      </div>

      {query.data && (
        <div className="panel">
          <h3>3. Multichannel Delivery History & Guard Outcomes</h3>
          <Table
            caption="Dispatched & Guard-Evaluated Reminders"
            rows={query.data.reminders}
            rowKey={(r) => r.id}
            columns={[
              { label: "Target", render: (r) => r.target_label },
              {
                label: "Channel",
                render: (r) => <Badge>{r.channel.toUpperCase()}</Badge>,
              },
              { label: "Destination", render: (r) => r.destination },
              {
                label: "Attempt",
                render: (r) => `#${r.attempt_number}`,
              },
              {
                label: "Status",
                render: (r) => <Badge>{r.status}</Badge>,
              },
              {
                label: "Provider Message ID",
                render: (r) => (
                  <code style={{ fontSize: "0.78rem" }}>
                    {r.provider_message_id || "—"}
                  </code>
                ),
              },
              {
                label: "Webhook Receipts & Slack Actions",
                render: (r) =>
                  r.status === "sent" || r.status === "delivered" ? (
                    <div
                      style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}
                    >
                      {r.status === "sent" && (
                        <Button
                          variant="secondary"
                          busy={webhookStatusMutation.isPending}
                          onClick={() =>
                            webhookStatusMutation.mutate({
                              reminderId: r.id,
                              event_source: "whatsapp_cloud_webhook",
                              delivery_status: "delivered",
                              interactive_action: "none",
                            })
                          }
                        >
                          Mark Delivered
                        </Button>
                      )}
                      <Button
                        variant="secondary"
                        busy={webhookStatusMutation.isPending}
                        onClick={() =>
                          webhookStatusMutation.mutate({
                            reminderId: r.id,
                            event_source:
                              r.channel === "slack"
                                ? "slack_interactive_webhook"
                                : "whatsapp_cloud_webhook",
                            delivery_status: "read",
                            interactive_action: "acknowledge",
                          })
                        }
                      >
                        {r.channel === "slack" ? "Slack Ack" : "WhatsApp Read"}
                      </Button>
                    </div>
                  ) : (
                    <Badge>{r.status}</Badge>
                  ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function SupplierPortalConnectors() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [vendorName, setVendorName] = useState("K-Electric / AWS Cloud Portal");
  const [portalKind, setPortalKind] = useState<
    | "web_totp"
    | "api_stripe"
    | "utility_portal"
    | "telecom_portal"
    | "cloud_portal"
  >("web_totp");
  const [portalUrl, setPortalUrl] = useState(
    "https://billing.vendor-portal.example/viewer/invoices",
  );
  const [loginUsername, setLoginUsername] = useState(
    "billing-viewer@balanc.local",
  );
  const [password, setPassword] = useState("ViewerSubUser#2026");
  const [totpSeed, setTotpSeed] = useState("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP");
  const [liveTotpInfo, setLiveTotpInfo] = useState<{
    code: string;
    remaining: number;
  } | null>(null);
  const [syncInvoiceNumber, setSyncInvoiceNumber] = useState("");
  const [syncTotalAmount, setSyncTotalAmount] = useState("425.00");
  const [syncTaxAmount, setSyncTaxAmount] = useState("25.00");
  const [syncMessage, setSyncMessage] = useState("");
  const [revokeTarget, setRevokeTarget] = useState<{
    id: string;
    vendor_name: string;
  } | null>(null);

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-supplier-portals"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/supplier-portals", {
          signal,
          params: { path },
        }),
      )) as unknown as PortalsData,
  });

  const generateSeedMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-portals/generate-totp-seed",
          {
            params: { path },
          },
        ),
      )) as unknown as {
        totp_seed: string;
        current_6_digit_code: string;
        seconds_remaining: number;
      },
    onSuccess: (res) => {
      setTotpSeed(res.totp_seed);
      setLiveTotpInfo({
        code: res.current_6_digit_code,
        remaining: res.seconds_remaining,
      });
    },
  });

  const createPortalMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p3/supplier-portals", {
          params: { path },
          body: {
            vendor_name: vendorName,
            portal_kind: portalKind,
            portal_url: portalUrl,
            login_username: loginUsername,
            password,
            totp_enabled: true,
            totp_seed: totpSeed,
            schedule_frequency: "monthly",
          },
        }),
      )) as unknown as {
        id: string;
        vendor_name: string;
        current_6_digit_code: string;
        seconds_remaining: number;
      },
    onSuccess: (res) => {
      setLiveTotpInfo({
        code: res.current_6_digit_code,
        remaining: res.seconds_remaining,
      });
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-portals"],
      });
    },
  });

  const syncPortalMutation = useMutation({
    mutationFn: async (connectionId: string) =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-portals/{connection_id}/sync",
          {
            params: {
              path: { business_id: b.id, connection_id: connectionId },
            },
            body: {
              external_invoice_id: syncInvoiceNumber.trim(),
              invoice_number: syncInvoiceNumber.trim(),
              total_amount: syncTotalAmount || "425.00",
              tax_amount: syncTaxAmount || "25.00",
              currency: b.currency || "USD",
              simulate_portal_changed: false,
            },
          },
        ),
      )) as unknown as {
        id: string;
        status: string;
        invoice_number: string;
        total_amount: string;
        currency: string;
        totp_6_digit_code_used: string;
        trade_draft_id: string | null;
      },
    onSuccess: (res) => {
      setSyncMessage(
        `Portal authenticated with 2FA code ${res.totp_6_digit_code_used || "API"} -> Ingested Invoice ${res.invoice_number} (${res.currency} ${res.total_amount}) into OCR Purchase Bill Draft (${res.status})!`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-portals"],
      });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (connectionId: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-portals/{connection_id}/revoke",
          {
            params: {
              path: { business_id: b.id, connection_id: connectionId },
            },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-portals"],
      });
    },
  });

  const promoteToPaymentMutation = useMutation({
    mutationFn: async (ingestionId: string) =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-portals/ingestions/{ingestion_id}/promote-to-payment",
          {
            params: {
              path: { business_id: b.id, ingestion_id: ingestionId },
            },
          },
        ),
      )) as unknown as {
        payment_id: string;
        bill_reference: string;
        supplier_name: string;
        amount: string;
        currency: string;
        state: string;
      },
    onSuccess: (res) => {
      setSyncMessage(
        `Promoted ${res.supplier_name} Invoice ${res.bill_reference} (${res.currency} ${res.amount}) into Supplier Payment Queue (Draft #${res.payment_id.slice(0, 8)} · State: ${res.state})!`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-payments"],
      });
    },
  });

  return (
    <div className="stack">
      <div className="panel">
        <h2>P3 Feature D · Authorized Supplier Portal Invoice Connectors</h2>
        <p className="muted">
          Connect read-only vendor billing sub-users (Method B: Utilities,
          Telecom, Software portals with 32-character Google Authenticator TOTP
          seed, or Method A: Stripe Vendor Invoices API). Credentials and TOTP
          seeds are Fernet-encrypted at rest; fetched invoices go through OCR
          into Purchase Bill Drafts for human review.
        </p>
      </div>

      <div className="panel">
        <h3>1. Connect Supplier Portal (with 32-Character 2FA TOTP Seed)</h3>
        <ErrorNotice
          error={createPortalMutation.error || generateSeedMutation.error}
        />
        <div className="form-grid">
          <Field label="Vendor / Supplier Name">
            <input
              value={vendorName}
              onChange={(e) => setVendorName(e.target.value)}
            />
          </Field>
          <Field label="Connector Method / Portal Type">
            <select
              value={portalKind}
              onChange={(e) =>
                setPortalKind(
                  e.target.value as
                    | "web_totp"
                    | "api_stripe"
                    | "utility_portal"
                    | "telecom_portal"
                    | "cloud_portal",
                )
              }
            >
              <option value="web_totp">
                Method B: Web Login Portal + 32-Char 2FA TOTP Seed
              </option>
              <option value="utility_portal">
                Method B: Utility Portal (K-Electric / ConEd + TOTP)
              </option>
              <option value="telecom_portal">
                Method B: Telecom Portal (PTCL / Verizon + TOTP)
              </option>
              <option value="cloud_portal">
                Method B: Cloud / SaaS Portal (AWS / DigitalOcean + TOTP)
              </option>
              <option value="api_stripe">
                Method A: Live Stripe Vendor Invoices API
              </option>
            </select>
          </Field>
          <Field label="Vendor Portal Login URL">
            <input
              value={portalUrl}
              onChange={(e) => setPortalUrl(e.target.value)}
            />
          </Field>
          <Field label="Viewer Sub-User Email / Login">
            <input
              value={loginUsername}
              onChange={(e) => setLoginUsername(e.target.value)}
            />
          </Field>
          <Field label="Sub-User Password (Encrypted at Rest)">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Field label="32-Character TOTP Secret Seed ('Can't scan barcode?' code)">
            <input
              value={totpSeed}
              onChange={(e) => setTotpSeed(e.target.value.toUpperCase())}
              placeholder="32-char Base32 seed e.g. JBSWY3DPEHPK3PXP..."
            />
          </Field>
        </div>
        <div
          style={{
            display: "flex",
            gap: "10px",
            flexWrap: "wrap",
            alignItems: "center",
          }}
        >
          <Button
            busy={createPortalMutation.isPending}
            onClick={() => createPortalMutation.mutate()}
          >
            Save & Authorize Portal Connector
          </Button>
          <Button
            variant="secondary"
            busy={generateSeedMutation.isPending}
            onClick={() => generateSeedMutation.mutate()}
          >
            Generate Valid 32-Char TOTP Seed
          </Button>
          {liveTotpInfo && (
            <Badge>
              Live 6-Digit Authenticator Code: {liveTotpInfo.code} (valid{" "}
              {liveTotpInfo.remaining}s)
            </Badge>
          )}
        </div>
      </div>

      {query.data && (
        <div className="panel">
          <h3>2. Authorized Portal Connections</h3>
          <ErrorNotice
            error={syncPortalMutation.error || revokeMutation.error}
          />
          <div className="form-grid" style={{ marginBottom: "12px" }}>
            <Field label="Portal Invoice # (Optional override)">
              <input
                value={syncInvoiceNumber}
                onChange={(e) => setSyncInvoiceNumber(e.target.value)}
                placeholder="Auto-detect from portal or enter e.g. INV-2026-991"
              />
            </Field>
            <Field label="Expected / Extracted Total Amount">
              <input
                type="number"
                step="0.01"
                value={syncTotalAmount}
                onChange={(e) => setSyncTotalAmount(e.target.value)}
              />
            </Field>
            <Field label="Expected / Extracted Tax Amount">
              <input
                type="number"
                step="0.01"
                value={syncTaxAmount}
                onChange={(e) => setSyncTaxAmount(e.target.value)}
              />
            </Field>
          </div>
          {syncMessage && (
            <p role="status" className="success-text">
              {syncMessage}
            </p>
          )}
          <Table
            caption="Connected Supplier Portals"
            rows={query.data.connections}
            rowKey={(c) => c.id}
            columns={[
              { label: "Vendor", render: (c) => c.vendor_name },
              {
                label: "Method",
                render: (c) => <Badge>{c.portal_kind}</Badge>,
              },
              { label: "Sub-User", render: (c) => c.login_username },
              {
                label: "Encrypted TOTP Seed & Live 6-Digit Code",
                render: (c) =>
                  c.totp_enabled ? (
                    <span>
                      <code>{c.totp_seed_preview}</code>{" "}
                      {c.live_totp_preview && (
                        <Badge>2FA: {c.live_totp_preview}</Badge>
                      )}
                    </span>
                  ) : (
                    "API Key"
                  ),
              },
              { label: "Status", render: (c) => <Badge>{c.status}</Badge> },
              {
                label: "Actions",
                render: (c) => (
                  <div style={{ display: "flex", gap: "8px" }}>
                    <Button
                      disabled={c.status !== "active"}
                      busy={syncPortalMutation.isPending}
                      onClick={() => syncPortalMutation.mutate(c.id)}
                    >
                      Fetch & OCR Invoice Now
                    </Button>
                    {c.status === "active" && (
                      <Button
                        variant="danger"
                        busy={revokeMutation.isPending}
                        onClick={() =>
                          setRevokeTarget({
                            id: c.id,
                            vendor_name: c.vendor_name,
                          })
                        }
                      >
                        Revoke
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
      )}

      {revokeTarget && (
        <Modal
          open={true}
          title="Revoke Supplier Portal Connector"
          description="Deactivate scheduled invoice syncs and stored TOTP credentials for this vendor."
          onClose={() => setRevokeTarget(null)}
        >
          <p>
            Are you sure you want to revoke the authorized portal connection for{" "}
            <strong>{revokeTarget.vendor_name}</strong>? Scheduled invoice syncs
            and stored TOTP credentials will be deactivated immediately.
          </p>
          <div
            style={{
              display: "flex",
              gap: "10px",
              justifyContent: "flex-end",
              marginTop: "16px",
            }}
          >
            <Button
              type="button"
              variant="secondary"
              onClick={() => setRevokeTarget(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="danger"
              busy={revokeMutation.isPending}
              onClick={() => {
                revokeMutation.mutate(revokeTarget.id, {
                  onSuccess: () => setRevokeTarget(null),
                });
              }}
            >
              Confirm Revoke
            </Button>
          </div>
        </Modal>
      )}

      {query.data && (
        <div className="panel">
          <h3>3. Ingested Supplier Invoices & OCR Purchase Bill Drafts</h3>
          <Table
            caption="Auto-Ingested Portal Invoices (Pending Human Draft Approval)"
            rows={query.data.ingestions}
            rowKey={(i) => i.id}
            columns={[
              { label: "Vendor", render: (i) => i.vendor_name },
              { label: "Invoice #", render: (i) => i.invoice_number },
              {
                label: "Amount (Tax)",
                render: (i) =>
                  `${i.currency} ${i.total_amount} (Tax: ${i.tax_amount})`,
              },
              {
                label: "OCR Confidence",
                render: (i) => `${(i.ocr_confidence * 100).toFixed(1)}%`,
              },
              {
                label: "Status",
                render: (i) => <Badge>{i.status}</Badge>,
              },
              {
                label: "Review Draft ID",
                render: (i) => (
                  <code style={{ fontSize: "0.78rem" }}>
                    {i.trade_draft_id
                      ? i.trade_draft_id.slice(0, 12) + "…"
                      : "—"}
                  </code>
                ),
              },
              {
                label: "End-to-End Pipeline",
                render: (i) => (
                  <Button
                    variant="secondary"
                    busy={promoteToPaymentMutation.isPending}
                    onClick={() => promoteToPaymentMutation.mutate(i.id)}
                  >
                    Promote to Payment Queue
                  </Button>
                ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function HumanApprovedSupplierPayments() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const access = useAccess();
  const canApprove = access?.role === "owner" || access?.role === "accountant";

  const [supplierName, setSupplierName] = useState("Amazon Web Services Inc.");
  const [holderName, setHolderName] = useState("Amazon Web Services Treasury");
  const [bankName, setBankName] = useState("JPMorgan Chase / Stripe Treasury");
  const [routing, setRouting] = useState("021000021");
  const [accountNumber, setAccountNumber] = useState("882910444321");
  const [rail, setRail] = useState<"stripe_live" | "ach_nacha" | "swift_wire">(
    "stripe_live",
  );

  const [selectedBeneficiaryId, setSelectedBeneficiaryId] = useState("");
  const [billRef, setBillRef] = useState("BILL-AWS-2026-114");
  const [payAmount, setPayAmount] = useState("425.00");
  const [stepUpCode, setStepUpCode] = useState("");
  const [activeRemittance, setActiveRemittance] = useState<string>("");
  const [statusNotice, setStatusNotice] = useState<string>("");
  const [confirmPayment, setConfirmPayment] = useState<
    PaymentsData["payments"][number] | null
  >(null);
  const [killSwitchModalOpen, setKillSwitchModalOpen] = useState(false);
  const [killSwitchReason, setKillSwitchReason] = useState(
    "Paused by Owner/Accountant via Emergency Kill Switch",
  );

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-supplier-payments"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/supplier-payments", {
          signal,
          params: { path },
        }),
      )) as unknown as PaymentsData,
  });

  const toggleKillSwitch = useMutation({
    mutationFn: (active: boolean) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-payments/controls",
          {
            params: { path },
            body: {
              kill_switch_active: active,
              kill_switch_reason: active ? killSwitchReason.trim() : "",
              single_payment_limit:
                query.data?.controls.single_payment_limit || "50000.00",
              dual_approval_threshold:
                query.data?.controls.dual_approval_threshold || "5000.00",
              default_rail: query.data?.controls.default_rail || "stripe_live",
            },
          },
        ),
      ),
    onSuccess: () => {
      setKillSwitchModalOpen(false);
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-payments"],
      });
    },
  });

  const createBeneficiary = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-payments/beneficiaries",
          {
            params: { path },
            body: {
              supplier_name: supplierName,
              account_holder_name: holderName,
              bank_name: bankName,
              routing_or_swift: routing,
              account_number: accountNumber,
              currency: b.currency || "USD",
              rail,
              out_of_band_verified: true,
              verification_reference:
                "Verified via vendor callback & signed W-9/IBAN letter",
            },
          },
        ),
      )) as unknown as { id: string },
    onSuccess: (res) => {
      setSelectedBeneficiaryId(res.id);
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-payments"],
      });
    },
  });

  const preparePayment = useMutation({
    mutationFn: async () => {
      const benId =
        selectedBeneficiaryId || query.data?.beneficiaries[0]?.id || "";
      const benObj = query.data?.beneficiaries.find((x) => x.id === benId);
      return (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-payments/prepare",
          {
            params: { path },
            body: {
              bill_reference: billRef,
              supplier_name: benObj?.supplier_name || supplierName,
              beneficiary_id: benId,
              amount: payAmount,
              currency: b.currency || "USD",
              rail,
              idempotency_key: "",
            },
          },
        ),
      )) as unknown as {
        id: string;
        state: string;
        requires_dual_approval: boolean;
      };
    },
    onSuccess: (res) => {
      setStatusNotice(
        `Payment draft prepared (State: ${res.state}, Dual Approval Required: ${res.requires_dual_approval ? "YES" : "NO"}). Awaiting human step-up approval.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-payments"],
      });
    },
  });

  const approveAndExecute = useMutation({
    mutationFn: async (paymentId: string) =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/supplier-payments/{payment_id}/approve-and-execute",
          {
            params: { path: { business_id: b.id, payment_id: paymentId } },
            body: {
              step_up_code: stepUpCode,
              approval_note: "Human-approved with step-up 2FA verification",
            },
          },
        ),
      )) as unknown as {
        id: string;
        state: string;
        provider_reference: string;
        remittance_advice: string;
        message?: string;
      },
    onSuccess: (res) => {
      if (res.remittance_advice) {
        setActiveRemittance(res.remittance_advice);
      }
      setStatusNotice(
        res.message ||
          `Payment settled! State: ${res.state} — Live Reference: ${res.provider_reference}`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-payments"],
      });
    },
  });

  const ctrl = query.data?.controls;

  return (
    <div className="stack">
      <div className="panel">
        <h2>P3 Feature E · Human-Approved Supplier Payment Execution</h2>
        <p className="muted">
          AI and bookkeepers may prepare payment drafts, but only an authorized
          human (Owner/Accountant) with Step-Up 2FA can approve and release
          funds. Includes Out-of-Band Beneficiary Verification, Account-Takeover
          Invalidation, Dual Approval thresholds, Emergency Kill Switch, and
          Live Stripe Payment Rail + NACHA ACH / SWIFT Wire settlement.
        </p>
        {ctrl && (
          <div
            style={{
              display: "flex",
              gap: "12px",
              flexWrap: "wrap",
              alignItems: "center",
              marginTop: "12px",
            }}
          >
            <Badge>
              Kill Switch:{" "}
              {ctrl.kill_switch_active ? "PAUSED (ACTIVE)" : "ARMED (NORMAL)"}
            </Badge>
            <Badge>Single Payment Limit: ${ctrl.single_payment_limit}</Badge>
            <Badge>
              Dual Approval Threshold: ${ctrl.dual_approval_threshold}
            </Badge>
            <Badge>
              Stripe Live Payment Rail:{" "}
              {ctrl.stripe_live_available ? "CONNECTED" : "SANDBOX"}
            </Badge>
            {canApprove && (
              <Button
                variant={ctrl.kill_switch_active ? "secondary" : "danger"}
                busy={toggleKillSwitch.isPending}
                onClick={() => {
                  if (ctrl.kill_switch_active) {
                    toggleKillSwitch.mutate(false);
                  } else {
                    setKillSwitchModalOpen(true);
                  }
                }}
              >
                {ctrl.kill_switch_active
                  ? "Deactivate Kill Switch (Resume Payouts)"
                  : "Activate Emergency Kill Switch"}
              </Button>
            )}
          </div>
        )}
        {killSwitchModalOpen && (
          <Modal
            open={true}
            title="Activate Emergency Payment Kill Switch"
            description="Immediately freeze all outbound supplier payment executions across Stripe, NACHA ACH, and SWIFT rails for this workspace."
            onClose={() => setKillSwitchModalOpen(false)}
          >
            <div className="stack" style={{ gap: "12px" }}>
              <Field label="Emergency Pause Reason (Audited)">
                <input
                  value={killSwitchReason}
                  onChange={(e) => setKillSwitchReason(e.target.value)}
                  required
                />
              </Field>
              <div className="p3-modal-actions">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setKillSwitchModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  disabled={!killSwitchReason.trim()}
                  busy={toggleKillSwitch.isPending}
                  onClick={() => toggleKillSwitch.mutate(true)}
                >
                  Confirm & Freeze All Payouts
                </Button>
              </div>
            </div>
          </Modal>
        )}
      </div>

      <div className="panel">
        <h3>1. Register & Verify Supplier Beneficiary (Out-of-Band Checked)</h3>
        <ErrorNotice error={createBeneficiary.error} />
        <div className="form-grid">
          <Field label="Supplier Name">
            <input
              value={supplierName}
              onChange={(e) => setSupplierName(e.target.value)}
            />
          </Field>
          <Field label="Account Holder Name">
            <input
              value={holderName}
              onChange={(e) => setHolderName(e.target.value)}
            />
          </Field>
          <Field label="Beneficiary Bank">
            <input
              value={bankName}
              onChange={(e) => setBankName(e.target.value)}
            />
          </Field>
          <Field label="ABA Routing / SWIFT BIC">
            <input
              value={routing}
              onChange={(e) => setRouting(e.target.value)}
            />
          </Field>
          <Field label="Account Number">
            <input
              value={accountNumber}
              onChange={(e) => setAccountNumber(e.target.value)}
            />
          </Field>
          <Field label="Settlement Rail">
            <select
              value={rail}
              onChange={(e) =>
                setRail(
                  e.target.value as "stripe_live" | "ach_nacha" | "swift_wire",
                )
              }
            >
              <option value="stripe_live">
                Live Stripe Payment & Payout Rail (Connected)
              </option>
              <option value="ach_nacha">
                Built-In NACHA ACH Bank Settlement Rail
              </option>
              <option value="swift_wire">
                Built-In SWIFT MT103 International Wire Rail
              </option>
            </select>
          </Field>
        </div>
        <Button
          busy={createBeneficiary.isPending}
          onClick={() => createBeneficiary.mutate()}
        >
          Verify & Save Supplier Beneficiary
        </Button>
      </div>

      <div className="panel">
        <h3>2. Prepare Supplier Payment Draft (Maker Step)</h3>
        <ErrorNotice error={preparePayment.error} />
        <div className="form-grid">
          <Field label="Verified Supplier Beneficiary">
            <select
              value={
                selectedBeneficiaryId || query.data?.beneficiaries[0]?.id || ""
              }
              onChange={(e) => setSelectedBeneficiaryId(e.target.value)}
            >
              {(query.data?.beneficiaries || []).map((ben) => (
                <option key={ben.id} value={ben.id}>
                  {ben.supplier_name} — {ben.bank_name} (
                  {ben.account_number_masked} v{ben.version})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Bill / Invoice Reference">
            <input
              value={billRef}
              onChange={(e) => setBillRef(e.target.value)}
            />
          </Field>
          <Field label="Amount">
            <input
              type="number"
              step="0.01"
              value={payAmount}
              onChange={(e) => setPayAmount(e.target.value)}
            />
          </Field>
          <Field label="Execution Rail">
            <select
              value={rail}
              onChange={(e) =>
                setRail(
                  e.target.value as "stripe_live" | "ach_nacha" | "swift_wire",
                )
              }
            >
              <option value="stripe_live">Live Stripe Payment Rail</option>
              <option value="ach_nacha">NACHA ACH Bank File</option>
              <option value="swift_wire">SWIFT MT103 Wire</option>
            </select>
          </Field>
        </div>
        <Button
          disabled={!query.data?.beneficiaries.length}
          busy={preparePayment.isPending}
          onClick={() => preparePayment.mutate()}
        >
          Prepare Payment Draft (Requires Human Approval)
        </Button>
      </div>

      {query.data && (
        <div className="panel">
          <h3>
            3. Human Approval & Step-Up 2FA Execution Queue (Checker Step)
          </h3>
          <ErrorNotice error={approveAndExecute.error} />
          {statusNotice && (
            <p role="status" className="success-text">
              {statusNotice}
            </p>
          )}
          <div style={{ maxWidth: "360px", marginBottom: "14px" }}>
            <Field label="Step-Up 2FA Code (6-digit TOTP or APPROVE)">
              <input
                value={stepUpCode}
                onChange={(e) => setStepUpCode(e.target.value)}
                placeholder="Enter 6-digit authenticator code"
              />
            </Field>
          </div>
          <Table
            caption="Supplier Payment Queue & Settlement History"
            rows={query.data.payments}
            rowKey={(p) => p.id}
            columns={[
              { label: "Supplier", render: (p) => p.supplier_name },
              { label: "Bill Ref", render: (p) => p.bill_reference },
              {
                label: "Amount",
                render: (p) => `${p.currency} ${p.amount}`,
              },
              { label: "Rail", render: (p) => <Badge>{p.rail}</Badge> },
              {
                label: "State",
                render: (p) => <Badge>{p.state}</Badge>,
              },
              {
                label: "Live Reference",
                render: (p) => (
                  <code style={{ fontSize: "0.78rem" }}>
                    {p.provider_reference ||
                      p.failure_reason ||
                      "Awaiting Approval"}
                  </code>
                ),
              },
              {
                label: "Ledger Journal",
                render: (p) => (
                  <code style={{ fontSize: "0.78rem" }}>
                    {p.settlement_journal_id
                      ? p.settlement_journal_id.slice(0, 10) + "…"
                      : "—"}
                  </code>
                ),
              },
              {
                label: "Human Action",
                render: (p) => (
                  <div style={{ display: "flex", gap: "8px" }}>
                    {(p.state === "prepared" || p.state === "first_approved") &&
                      canApprove && (
                        <Button
                          busy={approveAndExecute.isPending}
                          onClick={() => setConfirmPayment(p)}
                        >
                          {p.state === "first_approved"
                            ? "Final Dual Approve & Release"
                            : "Approve & Release Payment"}
                        </Button>
                      )}
                    {p.remittance_advice && (
                      <Button
                        variant="secondary"
                        onClick={() => setActiveRemittance(p.remittance_advice)}
                      >
                        View Remittance
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
          {confirmPayment && (
            <Modal
              open={true}
              title="Confirm Supplier Payment Settlement"
              description="Verify beneficiary bank account details and provide your Step-Up 2FA code to release funds."
              onClose={() => setConfirmPayment(null)}
            >
              <div className="stack" style={{ gap: "12px" }}>
                <p className="muted">
                  Review beneficiary bank details and provide your Step-Up 2FA
                  verification code before releasing funds.
                </p>
                <div
                  className="panel"
                  style={{
                    padding: "12px",
                    background: "var(--surface-alt)",
                  }}
                >
                  <div>
                    <strong>Supplier:</strong> {confirmPayment.supplier_name}
                  </div>
                  <div>
                    <strong>Bill Reference:</strong>{" "}
                    {confirmPayment.bill_reference}
                  </div>
                  <div>
                    <strong>Settlement Amount:</strong>{" "}
                    {confirmPayment.currency} {confirmPayment.amount}
                  </div>
                  <div>
                    <strong>Payment Rail:</strong>{" "}
                    <Badge>{confirmPayment.rail}</Badge>
                  </div>
                  <div>
                    <strong>Beneficiary Account:</strong>{" "}
                    <code>
                      {query.data.beneficiaries.find(
                        (ben) => ben.id === confirmPayment.beneficiary_id,
                      )?.account_number_masked || "Verified Account"}{" "}
                      (v{confirmPayment.beneficiary_version})
                    </code>
                  </div>
                </div>
                <Field label="Step-Up 2FA Code (6-digit TOTP or APPROVE)">
                  <input
                    value={stepUpCode}
                    onChange={(e) => setStepUpCode(e.target.value)}
                    placeholder="Enter 6-digit authenticator code"
                    autoFocus
                  />
                </Field>
                <div
                  style={{
                    display: "flex",
                    gap: "10px",
                    justifyContent: "flex-end",
                  }}
                >
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setConfirmPayment(null)}
                  >
                    Cancel
                  </Button>
                  <Button
                    type="button"
                    disabled={!stepUpCode.trim()}
                    busy={approveAndExecute.isPending}
                    onClick={() => {
                      approveAndExecute.mutate(confirmPayment.id, {
                        onSuccess: () => setConfirmPayment(null),
                      });
                    }}
                  >
                    Confirm & Execute Settlement
                  </Button>
                </div>
              </div>
            </Modal>
          )}
          {activeRemittance && (
            <div
              className="panel"
              style={{
                marginTop: "16px",
                background: "var(--surface-alt)",
              }}
            >
              <h4>Official Settlement Remittance Advice / Bank File</h4>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  fontSize: "0.82rem",
                  overflowX: "auto",
                }}
              >
                {activeRemittance}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ReconciliationSlaDashboard() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [slaHours, setSlaHours] = useState(24);
  const [chaseChannel, setChaseChannel] = useState<
    "auto" | "whatsapp" | "slack" | "email"
  >("auto");
  const [chaseNotice, setChaseNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-reconciliation-sla"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/reconciliation-sla", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        tracker: {
          id: string;
          target_date: string;
          sla_hours: number;
          started_at: string;
          deadline_at: string;
          completed_at: string | null;
          status: "on_track" | "at_risk" | "breached" | "completed";
          remaining_seconds: number;
          completion_percent: number;
          pending_bank_rows: number;
          missing_evidence_bills: number;
          pending_drafts: number;
          pending_close_tasks: number;
          total_blockers: number;
          chased_count: number;
          last_chased_at: string | null;
          checklist: Array<{
            key: string;
            label: string;
            open_count: number;
            completed: boolean;
          }>;
          unlinked_bills: Array<{
            id: string;
            number: string;
            total: string;
            currency: string;
            accounting_date: string;
          }>;
          unreviewed_bank_rows: Array<{
            id: string;
            description: string;
            amount: string;
            accounting_date: string;
          }>;
        };
      },
  });

  const startSlaMutation = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/reconciliation-sla/start",
          {
            params: { path },
            body: { sla_hours: slaHours },
          },
        ),
      ),
    onSuccess: () => {
      setChaseNotice(
        `One-Day Reconciliation SLA timer started (${slaHours}h window).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reconciliation-sla"],
      });
    },
  });

  const autoChaseMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/reconciliation-sla/auto-chase",
          {
            params: { path },
            body: {
              channel: chaseChannel,
              recipient_key: "ap-finance-owner",
            },
          },
        ),
      )) as unknown as {
        chased_count: number;
        total_blockers: number;
      },
    onSuccess: (res) => {
      setChaseNotice(
        `Auto-chased ${res.chased_count} SLA blocker notification(s) via ${chaseChannel.toUpperCase()} (${res.total_blockers} open item(s) tracked).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reconciliation-sla"],
      });
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reminders"],
      });
    },
  });

  const t = query.data?.tracker;
  const hrsLeft = t ? Math.floor(t.remaining_seconds / 3600) : 24;
  const minsLeft = t ? Math.floor((t.remaining_seconds % 3600) / 60) : 0;

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          Phase 3.1 (P3-H) · One-Day Reconciliation SLA Tracker & Auto-Chasing
        </h2>
        <p className="muted">
          Real-time 24-hour close and reconciliation SLA countdown across bank
          feed rows, unlinked supplier bill receipts, pending AI/OCR drafts, and
          period close tasks—with 1-click multichannel WhatsApp & Slack chasing
          for missing items.
        </p>
        {t && (
          <div
            style={{
              display: "flex",
              gap: "12px",
              flexWrap: "wrap",
              marginTop: "12px",
              alignItems: "center",
            }}
          >
            <Badge>SLA Status: {t.status.toUpperCase()}</Badge>
            <Badge>
              Countdown: {hrsLeft}h {minsLeft}m remaining (Target: {t.sla_hours}
              h)
            </Badge>
            <Badge>Completeness: {t.completion_percent}%</Badge>
            <Badge>Open Blockers: {t.total_blockers}</Badge>
            <Badge>Auto-Chases Sent: {t.chased_count}</Badge>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>1. Configure SLA Window & Trigger 1-Click Auto-Chasing</h3>
        <ErrorNotice
          error={startSlaMutation.error || autoChaseMutation.error}
        />
        <div className="form-grid">
          <Field label="Target SLA Window (Hours)">
            <input
              type="number"
              min={1}
              max={168}
              value={slaHours}
              onChange={(e) => setSlaHours(Number(e.target.value) || 24)}
            />
          </Field>
          <Field label="Auto-Chase Delivery Channel">
            <select
              value={chaseChannel}
              onChange={(e) =>
                setChaseChannel(
                  e.target.value as "auto" | "whatsapp" | "slack" | "email",
                )
              }
            >
              <option value="auto">
                Auto Cascade (WhatsApp -&gt; Slack -&gt; Email)
              </option>
              <option value="whatsapp">WhatsApp Cloud API</option>
              <option value="slack">Slack Workspace (#all-balanc)</option>
              <option value="email">Email Fallback</option>
            </select>
          </Field>
        </div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <Button
            busy={autoChaseMutation.isPending}
            onClick={() => autoChaseMutation.mutate()}
          >
            1-Click Auto-Chase Open SLA Blockers
          </Button>
          <Button
            variant="secondary"
            busy={startSlaMutation.isPending}
            onClick={() => startSlaMutation.mutate()}
          >
            Start / Reset {slaHours}h SLA Countdown
          </Button>
        </div>
        {chaseNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "10px" }}
          >
            {chaseNotice}
          </p>
        )}
      </div>

      {t && (
        <div className="panel">
          <h3>2. One-Day Reconciliation Completeness Checklist</h3>
          <div
            style={{
              height: "10px",
              borderRadius: "999px",
              background: "var(--surface-alt)",
              border: "1px solid var(--line)",
              overflow: "hidden",
              marginBottom: "14px",
            }}
            aria-label={`SLA completion ${t.completion_percent}%`}
          >
            <div
              style={{
                width: `${t.completion_percent}%`,
                height: "100%",
                background: "var(--accent)",
                transition: "width 0.25s ease",
              }}
            />
          </div>
          <Table
            caption="Reconciliation SLA Completeness Checklist"
            rows={t.checklist}
            rowKey={(c) => c.key}
            columns={[
              { label: "Control Gate", render: (c) => c.label },
              {
                label: "Open Items",
                render: (c) => <strong>{c.open_count}</strong>,
              },
              {
                label: "Gate Status",
                render: (c) => (
                  <Badge>{c.completed ? "COMPLETED" : "ACTION REQUIRED"}</Badge>
                ),
              },
            ]}
          />
          {t.unlinked_bills.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h4>Supplier Bills Missing Supporting Receipt / Evidence</h4>
              <Table
                caption="Supplier Bills Missing Evidence"
                rows={t.unlinked_bills}
                rowKey={(b) => b.id}
                columns={[
                  { label: "Bill #", render: (b) => b.number },
                  { label: "Date", render: (b) => b.accounting_date },
                  {
                    label: "Amount",
                    render: (b) => `${b.currency} ${b.total}`,
                  },
                ]}
              />
            </div>
          )}
          {t.unreviewed_bank_rows.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h4>Unreviewed Bank Feed Rows Blocking 24h Close</h4>
              <Table
                caption="Unreviewed Bank Feed Rows"
                rows={t.unreviewed_bank_rows}
                rowKey={(r) => r.id}
                columns={[
                  { label: "Date", render: (r) => r.accounting_date },
                  { label: "Description", render: (r) => r.description },
                  { label: "Amount", render: (r) => r.amount },
                ]}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function PayoutReconciliationHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [provider, setProvider] = useState<
    | "stripe_financial_connections"
    | "shopify_payouts"
    | "amazon_settlement"
    | "square_payouts"
  >("stripe_financial_connections");
  const [payoutRef, setPayoutRef] = useState("po_stripe_20261125_01");
  const [grossSales, setGrossSales] = useState("2000.00");
  const [processingFee, setProcessingFee] = useState("62.50");
  const [refundAmount, setRefundAmount] = useState("75.00");
  const [taxWithheld, setTaxWithheld] = useState("20.00");
  const [payoutNotice, setPayoutNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-payout-reconciliation"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/payout-reconciliation", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        feed_status: {
          stripe_financial_connections_live: boolean;
          provider_mode: string;
        };
        recent_bank_feed_rows: Array<{
          id: string;
          accounting_date: string;
          description: string;
          amount: string;
          external_id: string;
          state: string;
        }>;
        reconciliations: Array<{
          id: string;
          payout_reference: string;
          provider: string;
          payout_date: string;
          currency: string;
          gross_sales_amount: string;
          processing_fee_amount: string;
          refund_dispute_amount: string;
          tax_withheld_amount: string;
          net_payout_amount: string;
          journal_id: string | null;
          source_draft_id: string | null;
          split_lines: Array<{
            role: string;
            label: string;
            account_id: string;
            debit: string;
            credit: string;
          }>;
          status: string;
        }>;
      },
  });

  const syncFeedMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/payout-reconciliation/sync-bank-feed",
          {
            params: { path },
            body: { provider },
          },
        ),
      )) as unknown as {
        imported_count: number;
        skipped_duplicates: number;
      },
    onSuccess: (res) => {
      setPayoutNotice(
        `Synced ${res.imported_count} bank feed transaction(s) into Bank Rows (${res.skipped_duplicates} duplicate(s) skipped).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-payout-reconciliation"],
      });
    },
  });

  const reconcileMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/payout-reconciliation/reconcile",
          {
            params: { path },
            body: {
              payout_reference: payoutRef,
              provider,
              currency: b.currency || "USD",
              gross_sales_amount: grossSales,
              processing_fee_amount: processingFee,
              refund_dispute_amount: refundAmount,
              tax_withheld_amount: taxWithheld,
              idempotency_key: "",
            },
          },
        ),
      )) as unknown as {
        id: string;
        payout_reference: string;
        gross_sales_amount: string;
        net_payout_amount: string;
        status: string;
      },
    onSuccess: (res) => {
      setPayoutNotice(
        `Reconciled Payout ${res.payout_reference}: Gross ${res.gross_sales_amount} -> Net Bank Deposit ${res.net_payout_amount} (${res.status}).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-payout-reconciliation"],
      });
    },
  });

  const netPreview = (
    (Number(grossSales) || 0) -
    (Number(processingFee) || 0) -
    (Number(refundAmount) || 0) -
    (Number(taxWithheld) || 0)
  ).toFixed(2);

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          Phase 3.2 (P2-D1 & P3-J) · Live Bank Feed & Gross-to-Net Payout
          Reconciliation
        </h2>
        <p className="muted">
          Synchronize live bank feed transactions via Stripe Financial
          Connections and automatically split net merchant payouts into balanced
          double-entry lines (Gross Sales Revenue, Processing Fees, Customer
          Refunds/Disputes, Withheld Tax, and Net Bank Settlement).
        </p>
        <div
          style={{
            display: "flex",
            gap: "12px",
            flexWrap: "wrap",
            marginTop: "12px",
            alignItems: "center",
          }}
        >
          <Badge>
            Bank Feed Mode:{" "}
            {query.data?.feed_status.provider_mode?.toUpperCase() ||
              "STRIPE_SANDBOX_FEED"}
          </Badge>
          <Button
            variant="secondary"
            busy={syncFeedMutation.isPending}
            onClick={() => syncFeedMutation.mutate()}
          >
            Sync Live Bank Feed Into Bank Rows
          </Button>
        </div>
      </div>

      <div className="panel">
        <h3>1. Reconcile Gross-to-Net Merchant Payout Split</h3>
        <ErrorNotice
          error={syncFeedMutation.error || reconcileMutation.error}
        />
        <div className="form-grid">
          <Field label="Merchant Payout Provider">
            <select
              value={provider}
              onChange={(e) =>
                setProvider(
                  e.target.value as
                    | "stripe_financial_connections"
                    | "shopify_payouts"
                    | "amazon_settlement"
                    | "square_payouts",
                )
              }
            >
              <option value="stripe_financial_connections">
                Stripe Financial Connections & Payouts
              </option>
              <option value="shopify_payouts">Shopify Payments Payout</option>
              <option value="amazon_settlement">
                Amazon Seller Settlement
              </option>
              <option value="square_payouts">Square Merchant Payout</option>
            </select>
          </Field>
          <Field label="Payout Reference / Trace ID">
            <input
              value={payoutRef}
              onChange={(e) => setPayoutRef(e.target.value)}
            />
          </Field>
          <Field label="Gross Sales Revenue">
            <input
              type="number"
              step="0.01"
              value={grossSales}
              onChange={(e) => setGrossSales(e.target.value)}
            />
          </Field>
          <Field label="Merchant Processing Fees">
            <input
              type="number"
              step="0.01"
              value={processingFee}
              onChange={(e) => setProcessingFee(e.target.value)}
            />
          </Field>
          <Field label="Customer Refunds & Disputes">
            <input
              type="number"
              step="0.01"
              value={refundAmount}
              onChange={(e) => setRefundAmount(e.target.value)}
            />
          </Field>
          <Field label="Marketplace Tax Withheld">
            <input
              type="number"
              step="0.01"
              value={taxWithheld}
              onChange={(e) => setTaxWithheld(e.target.value)}
            />
          </Field>
        </div>
        <div
          style={{
            display: "flex",
            gap: "12px",
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <Button
            busy={reconcileMutation.isPending}
            onClick={() => reconcileMutation.mutate()}
          >
            Split & Reconcile Gross-to-Net Payout
          </Button>
          <Badge>
            Calculated Net Bank Deposit: {b.currency || "USD"} {netPreview}
          </Badge>
        </div>
        {payoutNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "10px" }}
          >
            {payoutNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>2. Reconciled Gross-to-Net Payout Splits</h3>
          <Table
            caption="Gross-to-Net Merchant Payout Reconciliations"
            rows={query.data.reconciliations}
            rowKey={(r) => r.id}
            columns={[
              { label: "Payout Ref", render: (r) => r.payout_reference },
              { label: "Provider", render: (r) => <Badge>{r.provider}</Badge> },
              {
                label: "Gross Sales",
                render: (r) => `${r.currency} ${r.gross_sales_amount}`,
              },
              {
                label: "Fees / Refunds / Tax",
                render: (r) =>
                  `-${r.processing_fee_amount} / -${r.refund_dispute_amount} / -${r.tax_withheld_amount}`,
              },
              {
                label: "Net Bank Deposit",
                render: (r) => (
                  <strong>
                    {r.currency} {r.net_payout_amount}
                  </strong>
                ),
              },
              {
                label: "Balanced Double-Entry Split Lines",
                render: (r) => (
                  <span style={{ fontSize: "0.78rem" }}>
                    {(r.split_lines || [])
                      .map((l) =>
                        Number(l.debit) > 0
                          ? `DR ${l.label}: ${l.debit}`
                          : `CR ${l.label}: ${l.credit}`,
                      )
                      .join(" · ")}
                  </span>
                ),
              },
              { label: "Status", render: (r) => <Badge>{r.status}</Badge> },
            ]}
          />
          {query.data.recent_bank_feed_rows.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h4>
                Recent Synced Bank Feed Transactions (Stripe Financial
                Connections)
              </h4>
              <Table
                caption="Recent Synced Bank Feed Transactions"
                rows={query.data.recent_bank_feed_rows}
                rowKey={(row) => row.id}
                columns={[
                  { label: "Date", render: (row) => row.accounting_date },
                  { label: "Description", render: (row) => row.description },
                  { label: "Amount", render: (row) => row.amount },
                  {
                    label: "Feed Trace ID",
                    render: (row) => <code>{row.external_id || "—"}</code>,
                  },
                  {
                    label: "Review State",
                    render: (row) => <Badge>{row.state}</Badge>,
                  },
                ]}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function VoiceNoteInstructionReview() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const access = useAccess();
  const canReview = access?.role === "owner" || access?.role === "accountant";

  const [audioFilename, setAudioFilename] = useState(
    "founder-voice-note-aws-bill.webm",
  );
  const [transcript, setTranscript] = useState(
    "Record supplier bill from Amazon Web Services for $480.00 including $30.00 tax for November production cluster hosting.",
  );
  const [voiceNotice, setVoiceNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-voice-instructions"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/voice-instructions", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        instructions: Array<{
          id: string;
          audio_filename: string;
          audio_duration_seconds: number;
          audio_sha256: string;
          transcript_text: string;
          extracted_action: string;
          extracted_counterparty: string;
          extracted_amount: string;
          extracted_tax_amount: string;
          extracted_currency: string;
          extracted_date: string;
          confidence_score: number;
          source_draft_id: string | null;
          journal_id: string | null;
          status: string;
          reviewer_note: string;
        }>;
      },
  });

  const ingestMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p3/voice-instructions", {
          params: { path },
          body: {
            audio_filename: audioFilename,
            audio_mime_type: "audio/webm",
            audio_duration_seconds: 14,
            audio_base64: "",
            transcript_text: transcript,
            extracted_counterparty: "",
            extracted_currency: b.currency || "USD",
          },
        }),
      )) as unknown as {
        id: string;
        extracted_action: string;
        extracted_counterparty: string;
        extracted_amount: string;
        extracted_currency: string;
        source_draft_id: string | null;
      },
    onSuccess: (res) => {
      setVoiceNotice(
        `Extracted ${res.extracted_action.toUpperCase()} for ${res.extracted_counterparty} (${res.extracted_currency} ${res.extracted_amount}) -> Created Review Draft (${res.source_draft_id?.slice(0, 8) || "pending"}).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-voice-instructions"],
      });
    },
  });

  const reviewMutation = useMutation({
    mutationFn: ({
      id,
      decision,
    }: {
      id: string;
      decision: "approve" | "reject";
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/voice-instructions/{instruction_id}/review",
          {
            params: { path: { business_id: b.id, instruction_id: id } },
            body: {
              decision,
              reviewer_note:
                decision === "approve"
                  ? "Verified voice transcript and approved by accountant"
                  : "Rejected during human review",
            },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-voice-instructions"],
      });
    },
  });

  return (
    <div className="stack">
      <div className="panel">
        <h2>Phase 3.3 (P3-C) · Voice-Note Accounting Instruction Review</h2>
        <p className="muted">
          Capture or upload spoken accounting instructions, transcribe and
          extract structured bookkeeping entities (action type, counterparty,
          amount, tax, confidence score), and route them into linked Source
          Drafts for mandatory human accountant review before posting.
        </p>
      </div>

      <div className="panel">
        <h3>1. Upload or Capture Voice-Note Accounting Instruction</h3>
        <ErrorNotice error={ingestMutation.error || reviewMutation.error} />
        <div className="form-grid">
          <Field label="Voice Note Audio File / Recording Name">
            <input
              value={audioFilename}
              onChange={(e) => setAudioFilename(e.target.value)}
            />
          </Field>
          <Field label="Upload Audio Recording (.webm / .mp3 / .wav)">
            <input
              type="file"
              accept="audio/*"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) {
                  setAudioFilename(f.name);
                }
              }}
            />
          </Field>
        </div>
        <div
          style={{
            display: "flex",
            gap: "8px",
            flexWrap: "wrap",
            marginBottom: "10px",
          }}
        >
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setAudioFilename("founder-voice-aws-bill.webm");
              setTranscript(
                "Record supplier bill from Amazon Web Services for $480.00 including $30.00 tax for November production cluster hosting.",
              );
            }}
          >
            Sample: Supplier Bill ($480)
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setAudioFilename("founder-voice-client-invoice.webm");
              setTranscript(
                "Issue customer invoice to Acme Enterprise Corp for $3500.00 including $250.00 tax for Q4 financial advisory retainer.",
              );
            }}
          >
            Sample: Customer Invoice ($3,500)
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setAudioFilename("founder-voice-kelectric-expense.webm");
              setTranscript(
                "Paid expense to K-Electric Utility for $420.00 including $20.00 tax for October office electricity.",
              );
            }}
          >
            Sample: Utility Expense ($420)
          </Button>
        </div>
        <Field label="Transcribed Voice Instruction (or Spoken Prompt)">
          <textarea
            rows={2}
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
          />
        </Field>
        <Button
          busy={ingestMutation.isPending}
          onClick={() => ingestMutation.mutate()}
        >
          Transcribe, Extract & Create Review Draft
        </Button>
        {voiceNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "10px" }}
          >
            {voiceNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>2. Extracted Voice Instructions Queue (Human Review Gate)</h3>
          <Table
            caption="Voice-Note Accounting Instructions"
            rows={query.data.instructions}
            rowKey={(i) => i.id}
            columns={[
              {
                label: "Action",
                render: (i) => <Badge>{i.extracted_action}</Badge>,
              },
              {
                label: "Counterparty",
                render: (i) => i.extracted_counterparty,
              },
              {
                label: "Amount (Tax)",
                render: (i) =>
                  `${i.extracted_currency} ${i.extracted_amount} (Tax: ${i.extracted_tax_amount})`,
              },
              {
                label: "Transcript",
                render: (i) => i.transcript_text,
              },
              {
                label: "Confidence",
                render: (i) => `${(i.confidence_score * 100).toFixed(1)}%`,
              },
              { label: "Status", render: (i) => <Badge>{i.status}</Badge> },
              {
                label: "Accountant Action",
                render: (i) =>
                  i.status === "pending_review" && canReview ? (
                    <div style={{ display: "flex", gap: "8px" }}>
                      <Button
                        busy={reviewMutation.isPending}
                        onClick={() =>
                          reviewMutation.mutate({
                            id: i.id,
                            decision: "approve",
                          })
                        }
                      >
                        Approve
                      </Button>
                      <Button
                        variant="danger"
                        busy={reviewMutation.isPending}
                        onClick={() =>
                          reviewMutation.mutate({
                            id: i.id,
                            decision: "reject",
                          })
                        }
                      >
                        Reject
                      </Button>
                    </div>
                  ) : (
                    <code>
                      {i.journal_id?.slice(0, 8) ||
                        i.source_draft_id?.slice(0, 8) ||
                        i.status}
                    </code>
                  ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function TenantEncryptionKeyVault() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const access = useAccess();
  const isOwner = access?.role === "owner";

  const [destroyModalOpen, setDestroyModalOpen] = useState(false);
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [destroyReason, setDestroyReason] = useState(
    "Tenant offboarding / GDPR cryptographic erasure drill",
  );
  const [keyNotice, setKeyNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-encryption-keys"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/encryption-keys", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        envelope_status: {
          master_kek_configured: boolean;
          active_key_version: number | null;
          active_key_status: string;
          protected_records_count: number;
          provably_shredded: boolean;
        };
        keys: Array<{
          id: string;
          key_version: number;
          algorithm: string;
          dek_fingerprint_sha256: string;
          wrapped_dek_present: boolean;
          status: string;
          encrypted_records_count: number;
          destruction_reason: string;
          destruction_certificate_sha256: string;
          created_at: string;
          rotated_at: string | null;
          destroyed_at: string | null;
        }>;
      },
  });

  const rotateMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p3/encryption-keys/rotate", {
          params: { path },
        }),
      )) as unknown as {
        key_version: number;
        dek_fingerprint_sha256: string;
      },
    onSuccess: (res) => {
      setKeyNotice(
        `Rotated tenant Data Encryption Key to v${res.key_version} (Fingerprint: ${res.dek_fingerprint_sha256.slice(0, 16)}…).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-encryption-keys"],
      });
    },
  });

  const destroyMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/encryption-keys/destroy",
          {
            params: { path },
            body: {
              confirmation: confirmPhrase,
              reason: destroyReason,
            },
          },
        ),
      )) as unknown as {
        destroyed_key_version: number;
        destruction_certificate_sha256: string;
        shredded_records_count: number;
      },
    onSuccess: (res) => {
      setDestroyModalOpen(false);
      setConfirmPhrase("");
      setKeyNotice(
        `Cryptographically destroyed Tenant DEK v${res.destroyed_key_version} (${res.shredded_records_count} record(s) shredded). Destruction Certificate SHA-256: ${res.destruction_certificate_sha256}`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-encryption-keys"],
      });
    },
  });

  const es = query.data?.envelope_status;

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          Phase 3.4 (P3-G) · Per-Tenant Envelope Encryption Keys & Provable Key
          Destruction
        </h2>
        <p className="muted">
          Each business workspace has a dedicated 256-bit Data Encryption Key
          (DEK) wrapped by the platform Master Key Encryption Key (KEK).
          Supports online key rotation and provable cryptographic key
          destruction (crypto-shredding) with a tamper-evident SHA-256
          destruction certificate.
        </p>
        {es && (
          <div
            style={{
              display: "flex",
              gap: "12px",
              flexWrap: "wrap",
              marginTop: "12px",
              alignItems: "center",
            }}
          >
            <Badge>
              Master KEK: {es.master_kek_configured ? "ACTIVE" : "UNCONFIGURED"}
            </Badge>
            <Badge>
              Active Tenant DEK:{" "}
              {es.active_key_version
                ? `v${es.active_key_version} (${es.active_key_status.toUpperCase()})`
                : "DESTROYED (SHREDDED)"}
            </Badge>
            <Badge>Protected Secrets: {es.protected_records_count}</Badge>
            <Button
              variant="secondary"
              busy={rotateMutation.isPending}
              onClick={() => rotateMutation.mutate()}
            >
              Rotate Tenant DEK (v+1)
            </Button>
            {isOwner && !es.provably_shredded && (
              <Button
                variant="danger"
                onClick={() => setDestroyModalOpen(true)}
              >
                Cryptographically Destroy Tenant Key
              </Button>
            )}
          </div>
        )}
        <ErrorNotice error={rotateMutation.error || destroyMutation.error} />
        {keyNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "10px" }}
          >
            {keyNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Tenant DEK Lifecycle & Cryptographic Destruction Certificates</h3>
          <Table
            caption="Per-Tenant Envelope Encryption Key Versions"
            rows={query.data.keys}
            rowKey={(k) => k.id}
            columns={[
              { label: "Version", render: (k) => `v${k.key_version}` },
              { label: "Algorithm", render: (k) => k.algorithm },
              {
                label: "DEK Fingerprint (SHA-256)",
                render: (k) => (
                  <code>{k.dek_fingerprint_sha256.slice(0, 16)}…</code>
                ),
              },
              {
                label: "Wrapped Key Material",
                render: (k) =>
                  k.wrapped_dek_present
                    ? "Wrapped by Master KEK"
                    : "WIPED (0 bytes)",
              },
              { label: "Status", render: (k) => <Badge>{k.status}</Badge> },
              {
                label: "Destruction Certificate SHA-256",
                render: (k) => (
                  <code>
                    {k.destruction_certificate_sha256
                      ? k.destruction_certificate_sha256.slice(0, 20) + "…"
                      : "—"}
                  </code>
                ),
              },
            ]}
          />
        </div>
      )}

      {destroyModalOpen && (
        <Modal
          open={true}
          title="Confirm Cryptographic Tenant Key Destruction"
          description="Permanently shred the wrapped Data Encryption Key (DEK) for this workspace and emit an immutable SHA-256 destruction certificate."
          onClose={() => setDestroyModalOpen(false)}
        >
          <div className="stack" style={{ gap: "12px" }}>
            <Field label="Destruction Reason / Audit Reference">
              <input
                value={destroyReason}
                onChange={(e) => setDestroyReason(e.target.value)}
              />
            </Field>
            <Field label="Type DESTROY-TENANT-KEY to confirm">
              <input
                value={confirmPhrase}
                onChange={(e) => setConfirmPhrase(e.target.value)}
                placeholder="DESTROY-TENANT-KEY"
                autoFocus
              />
            </Field>
            <div
              style={{
                display: "flex",
                gap: "10px",
                justifyContent: "flex-end",
              }}
            >
              <Button
                type="button"
                variant="secondary"
                onClick={() => setDestroyModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="danger"
                disabled={confirmPhrase.trim() !== "DESTROY-TENANT-KEY"}
                busy={destroyMutation.isPending}
                onClick={() => destroyMutation.mutate()}
              >
                Confirm Cryptographic Key Destruction
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export function AiOcrCostBenchmarkHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [locale, setLocale] = useState<
    "en_US" | "ur_PK" | "ar_AE" | "es_ES" | "fr_FR"
  >("ur_PK");
  const [orientation, setOrientation] = useState<
    | "0_deg_clean"
    | "90_deg_rotated"
    | "180_deg_upside_down"
    | "270_deg_rotated"
    | "skewed_thermal_receipt"
  >("90_deg_rotated");
  const [routingTier, setRoutingTier] = useState<
    | "hybrid_rule_cache_local_ocr"
    | "deterministic_template_cache"
    | "cloud_vlm_fallback"
  >("hybrid_rule_cache_local_ocr");
  const [simulateBlur, setSimulateBlur] = useState(false);
  const [benchNotice, setBenchNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-ai-benchmarks"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/ai-benchmarks", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        summary: {
          baseline_p2_cost_per_doc_usd: string;
          current_p3_cost_per_doc_usd: string;
          cost_reduction_percent: number;
          target_reduction_percent: number;
          target_met: boolean;
        };
        runs: Array<{
          id: string;
          language_locale: string;
          scan_orientation: string;
          ocr_char_accuracy: number;
          field_extraction_f1: number;
          confidence_score: number;
          abstained_to_manual_review: boolean;
          baseline_p2_cost_usd: string;
          p3_routed_cost_usd: string;
          cost_reduction_percent: number;
          routing_tier: string;
        }>;
      },
  });

  const evalMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p3/ai-benchmarks/evaluate", {
          params: { path },
          body: {
            language_locale: locale,
            scan_orientation: orientation,
            simulate_low_quality_blur: simulateBlur,
            routing_tier: routingTier,
          },
        }),
      )) as unknown as {
        language_locale: string;
        scan_orientation: string;
        field_extraction_f1: number;
        cost_reduction_percent: number;
        abstained_to_manual_review: boolean;
      },
    onSuccess: (res) => {
      setBenchNotice(
        `Evaluated ${res.language_locale} (${res.scan_orientation}): Field F1 ${(res.field_extraction_f1 * 100).toFixed(1)}%, Cost Reduction ${res.cost_reduction_percent}% (${res.abstained_to_manual_review ? "Abstained to Human Review" : "Auto-Draft Passed"}).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-ai-benchmarks"],
      });
    },
  });

  const s = query.data?.summary;

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          P3-F (P3-F1 & P3-F3) · Multilingual/Rotated OCR Matrix & 50% AI
          Cost-Reduction Benchmark
        </h2>
        <p className="muted">
          Evaluates multilingual invoice/receipt OCR across rotated/skewed scans
          with low-confidence abstention gates, and verifies the &ge;50% AI
          per-document inference cost reduction via deterministic template
          caching and quantized local PaddleOCR routing.
        </p>
        {s && (
          <div className="p3-badge-row">
            <Badge>
              P2 Baseline Cost: ${s.baseline_p2_cost_per_doc_usd}/doc
            </Badge>
            <Badge>P3 Routed Cost: ${s.current_p3_cost_per_doc_usd}/doc</Badge>
            <Badge>
              AI Cost Reduction: {s.cost_reduction_percent}% (Target &ge;
              {s.target_reduction_percent}%:{" "}
              {s.target_met ? "MET" : "IN PROGRESS"})
            </Badge>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>1. Run Multilingual & Rotated Scan Evaluation</h3>
        <ErrorNotice error={evalMutation.error} />
        <div className="form-grid">
          <Field label="Document Language & Script Locale">
            <select
              value={locale}
              onChange={(e) => setLocale(e.target.value as typeof locale)}
            >
              <option value="ur_PK">Urdu / Pakistan (ur_PK · PKR)</option>
              <option value="ar_AE">Arabic / UAE (ar_AE · AED)</option>
              <option value="en_US">English / US (en_US · USD)</option>
              <option value="es_ES">Spanish / EU (es_ES · EUR)</option>
              <option value="fr_FR">French / EU (fr_FR · EUR)</option>
            </select>
          </Field>
          <Field label="Scan Orientation & Physical Condition">
            <select
              value={orientation}
              onChange={(e) =>
                setOrientation(e.target.value as typeof orientation)
              }
            >
              <option value="90_deg_rotated">90° Rotated Mobile Photo</option>
              <option value="180_deg_upside_down">180° Upside-Down Scan</option>
              <option value="270_deg_rotated">270° Rotated Scan</option>
              <option value="skewed_thermal_receipt">
                Skewed Thermal POS Receipt
              </option>
              <option value="0_deg_clean">0° Clean Flatbed / PDF</option>
            </select>
          </Field>
          <Field label="P3 Cost-Optimization Routing Tier">
            <select
              value={routingTier}
              onChange={(e) =>
                setRoutingTier(e.target.value as typeof routingTier)
              }
            >
              <option value="hybrid_rule_cache_local_ocr">
                Hybrid Rule Cache + Local Quantized OCR (65.5% Savings)
              </option>
              <option value="deterministic_template_cache">
                Deterministic Vendor Template Cache (84.5% Savings)
              </option>
              <option value="cloud_vlm_fallback">
                Cloud VLM Escalation Tier (51.2% Savings)
              </option>
            </select>
          </Field>
        </div>
        <div className="p3-actions-row">
          <label>
            <input
              type="checkbox"
              checked={simulateBlur}
              onChange={(e) => setSimulateBlur(e.target.checked)}
            />{" "}
            Simulate Degraded / Blurred Scan (Test Abstention Gate &lt; 85%)
          </label>
          <Button
            busy={evalMutation.isPending}
            onClick={() => evalMutation.mutate()}
          >
            Evaluate OCR Quality & Cost Benchmark
          </Button>
        </div>
        {benchNotice && (
          <p role="status" className="success-text">
            {benchNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>2. Recorded Multilingual OCR & Cost-Reduction Runs</h3>
          <Table
            caption="Multilingual OCR and AI Cost Benchmark Runs"
            rows={query.data.runs}
            rowKey={(r) => r.id}
            columns={[
              {
                label: "Locale",
                render: (r) => <Badge>{r.language_locale}</Badge>,
              },
              { label: "Orientation", render: (r) => r.scan_orientation },
              {
                label: "Char Acc / Field F1",
                render: (r) =>
                  `${(r.ocr_char_accuracy * 100).toFixed(1)}% / ${(r.field_extraction_f1 * 100).toFixed(1)}%`,
              },
              {
                label: "Cost (P2 -> P3)",
                render: (r) =>
                  `$${r.baseline_p2_cost_usd} -> $${r.p3_routed_cost_usd} (-${r.cost_reduction_percent}%)`,
              },
              {
                label: "Safety Gate",
                render: (r) => (
                  <Badge>
                    {r.abstained_to_manual_review
                      ? "ABSTAINED TO MANUAL REVIEW"
                      : "PASSED"}
                  </Badge>
                ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function AccountingExpansionHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [workflowKind, setWorkflowKind] = useState<
    | "contract_revenue_schedule"
    | "payroll_clearing_split"
    | "short_pay_resolution"
  >("contract_revenue_schedule");
  const [reference, setReference] = useState("SAAS-CONTRACT-2026-Q4");
  const [counterparty, setCounterparty] = useState("Acme Enterprise Corp");
  const [totalAmount, setTotalAmount] = useState("12000.00");
  const [months, setMonths] = useState(12);
  const [installmentNum, setInstallmentNum] = useState(1);
  const [employerTax, setEmployerTax] = useState("850.00");
  const [employeeWithholding, setEmployeeWithholding] = useState("1400.00");
  const [benefitsDeduction, setBenefitsDeduction] = useState("400.00");
  const [settledAmount, setSettledAmount] = useState("11700.00");
  const [resolutionPolicy, setResolutionPolicy] = useState<
    "early_payment_discount" | "bank_fee_writeoff" | "disputed_credit_memo"
  >("early_payment_discount");
  const [expNotice, setExpNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-accounting-expansion"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/accounting-expansion", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        workflows: Array<{
          id: string;
          workflow_kind: string;
          reference: string;
          counterparty_name: string;
          accounting_date: string;
          currency: string;
          total_amount: string;
          recognized_or_settled_amount: string;
          deferred_or_variance_amount: string;
          resolution_policy: string;
          journal_lines: Array<{
            label: string;
            debit: string;
            credit: string;
          }>;
          source_draft_id: string | null;
          status: string;
        }>;
      },
  });

  const executeMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p3/accounting-expansion/execute",
          {
            params: { path },
            body: {
              workflow_kind: workflowKind,
              reference,
              counterparty_name: counterparty,
              currency: b.currency || "USD",
              total_amount: totalAmount,
              months,
              installment_number: installmentNum,
              employer_tax_amount: employerTax,
              employee_withholding_amount: employeeWithholding,
              benefits_deduction_amount: benefitsDeduction,
              settled_amount:
                workflowKind === "short_pay_resolution" ? settledAmount : null,
              resolution_policy: resolutionPolicy,
            },
          },
        ),
      )) as unknown as {
        workflow_kind: string;
        reference: string;
        recognized_or_settled_amount: string;
        deferred_or_variance_amount: string;
        currency: string;
      },
    onSuccess: (res) => {
      setExpNotice(
        `Prepared ${res.workflow_kind} (${res.reference}): Recognized/Settled ${res.currency} ${res.recognized_or_settled_amount} · Deferred/Variance ${res.currency} ${res.deferred_or_variance_amount}.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-accounting-expansion"],
      });
    },
  });

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          P3-I (P3-I1) · Contract Revenue Schedules, Payroll Clearing Splits &
          Short-Pay Resolution
        </h2>
        <p className="muted">
          Automated ratable contract revenue recognition with penny-exact final
          installment rounding, gross-to-net payroll clearing splits (wages,
          employer taxes, withholdings, benefits), and customer short-pay
          discrepancy resolution.
        </p>
      </div>

      <div className="panel">
        <h3>1. Prepare Accounting Expansion Schedule or Split</h3>
        <ErrorNotice error={executeMutation.error} />
        <div className="form-grid">
          <Field label="Accounting Expansion Module">
            <select
              value={workflowKind}
              onChange={(e) =>
                setWorkflowKind(e.target.value as typeof workflowKind)
              }
            >
              <option value="contract_revenue_schedule">
                Contract Revenue Recognition Schedule (ASC 606 / IFRS 15)
              </option>
              <option value="payroll_clearing_split">
                Payroll Clearing & Tax/Benefit Split
              </option>
              <option value="short_pay_resolution">
                Customer Invoice Short-Pay Resolution
              </option>
            </select>
          </Field>
          <Field label="Contract / Payroll / Invoice Reference">
            <input
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
          </Field>
          <Field label="Counterparty / Payroll Batch Name">
            <input
              value={counterparty}
              onChange={(e) => setCounterparty(e.target.value)}
            />
          </Field>
          <Field label="Total Contract / Gross Payroll / Invoice Amount">
            <input
              type="number"
              step="0.01"
              value={totalAmount}
              onChange={(e) => setTotalAmount(e.target.value)}
            />
          </Field>
          {workflowKind === "contract_revenue_schedule" && (
            <>
              <Field label="Contract Term (Months)">
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={months}
                  onChange={(e) => setMonths(Number(e.target.value) || 12)}
                />
              </Field>
              <Field label="Installment Number to Recognize">
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={installmentNum}
                  onChange={(e) =>
                    setInstallmentNum(Number(e.target.value) || 1)
                  }
                />
              </Field>
            </>
          )}
          {workflowKind === "payroll_clearing_split" && (
            <>
              <Field label="Employer Payroll Taxes">
                <input
                  type="number"
                  step="0.01"
                  value={employerTax}
                  onChange={(e) => setEmployerTax(e.target.value)}
                />
              </Field>
              <Field label="Employee Withholding Tax">
                <input
                  type="number"
                  step="0.01"
                  value={employeeWithholding}
                  onChange={(e) => setEmployeeWithholding(e.target.value)}
                />
              </Field>
              <Field label="Employee Benefits / Provident Deduction">
                <input
                  type="number"
                  step="0.01"
                  value={benefitsDeduction}
                  onChange={(e) => setBenefitsDeduction(e.target.value)}
                />
              </Field>
            </>
          )}
          {workflowKind === "short_pay_resolution" && (
            <>
              <Field label="Cash Received (Short-Paid Settlement)">
                <input
                  type="number"
                  step="0.01"
                  value={settledAmount}
                  onChange={(e) => setSettledAmount(e.target.value)}
                />
              </Field>
              <Field label="Short-Pay Variance Policy">
                <select
                  value={resolutionPolicy}
                  onChange={(e) =>
                    setResolutionPolicy(
                      e.target.value as typeof resolutionPolicy,
                    )
                  }
                >
                  <option value="early_payment_discount">
                    Early Payment Cash Discount Allowed
                  </option>
                  <option value="bank_fee_writeoff">
                    Intermediary Bank Wire Fee Write-Off
                  </option>
                  <option value="disputed_credit_memo">
                    Disputed Allowance / Credit Adjustment
                  </option>
                </select>
              </Field>
            </>
          )}
        </div>
        <Button
          busy={executeMutation.isPending}
          onClick={() => executeMutation.mutate()}
        >
          Compute Balanced Journal Split & Create Review Draft
        </Button>
        {expNotice && (
          <p role="status" className="success-text">
            {expNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>2. Executed Accounting Expansion Schedules & Splits</h3>
          <Table
            caption="Accounting Expansion Workflows"
            rows={query.data.workflows}
            rowKey={(w) => w.id}
            columns={[
              {
                label: "Module",
                render: (w) => <Badge>{w.workflow_kind}</Badge>,
              },
              { label: "Reference", render: (w) => w.reference },
              {
                label: "Total",
                render: (w) => `${w.currency} ${w.total_amount}`,
              },
              {
                label: "Recognized / Settled",
                render: (w) =>
                  `${w.currency} ${w.recognized_or_settled_amount}`,
              },
              {
                label: "Deferred / Variance",
                render: (w) => `${w.currency} ${w.deferred_or_variance_amount}`,
              },
              {
                label: "Balanced Lines",
                render: (w) => (
                  <span style={{ fontSize: "0.78rem" }}>
                    {(w.journal_lines || [])
                      .map((l) =>
                        Number(l.debit) > 0
                          ? `DR ${l.label}: ${l.debit}`
                          : `CR ${l.label}: ${l.credit}`,
                      )
                      .join(" · ")}
                  </span>
                ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function EnterpriseSsoCellHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [protocol, setProtocol] = useState<"oidc" | "saml2">("oidc");
  const [idpProvider, setIdpProvider] = useState<
    "okta" | "entra_id" | "google_workspace" | "onelogin"
  >("okta");
  const [issuerUrl, setIssuerUrl] = useState(
    "https://acme.okta.com/oauth2/default",
  );
  const [ssoClientId, setSsoClientId] = useState("0oa1balancenterprise");
  const [ssoSecret, setSsoSecret] = useState("");
  const [emailDomain, setEmailDomain] = useState("acme-enterprise.example");
  const [regionCell, setRegionCell] = useState<
    | "us-east-va-cell-1"
    | "eu-central-fra-cell-1"
    | "me-central-dxb-cell-1"
    | "ap-south-khi-cell-1"
  >("us-east-va-cell-1");
  const [ssoNotice, setSsoNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p3-enterprise-sso-cell"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p3/enterprise-sso-cell", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        config: {
          protocol: string;
          idp_provider: string;
          issuer_url: string;
          sso_client_id: string;
          sso_secret_configured: boolean;
          email_domain: string;
          enforce_sso: boolean;
          jit_default_role: string;
          region_cell: string;
          data_residency_locked: boolean;
          cross_region_egress_blocked: boolean;
          residency_attestation_sha256: string;
          last_verified_at: string | null;
        };
      },
  });

  const saveMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p3/enterprise-sso-cell", {
          params: { path },
          body: {
            protocol,
            idp_provider: idpProvider,
            issuer_url: issuerUrl,
            sso_client_id: ssoClientId,
            sso_client_secret_or_cert: ssoSecret,
            email_domain: emailDomain,
            enforce_sso: true,
            jit_default_role: "reviewer",
            region_cell: regionCell,
            data_residency_locked: true,
            cross_region_egress_blocked: true,
          },
        }),
      )) as unknown as {
        region_cell: string;
        residency_attestation_sha256: string;
      },
    onSuccess: (res) => {
      setSsoNotice(
        `Enterprise SSO & Regional Cell pinned to ${res.region_cell}. Attestation SHA-256: ${res.residency_attestation_sha256.slice(0, 20)}…`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-enterprise-sso-cell"],
      });
    },
  });

  const cfg = query.data?.config;

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          P3-K · Enterprise SSO (OIDC / SAML 2.0) & Regional Data-Residency Cell
          Isolation
        </h2>
        <p className="muted">
          Bind workspace identity to enterprise IdPs (Okta, Microsoft Entra ID,
          Google Workspace, OneLogin) and pin tenant storage and compute to an
          isolated regional deployment cell with cross-region egress blocking
          and cryptographic residency attestation.
        </p>
        {cfg && (
          <div className="p3-badge-row">
            <Badge>
              SSO Protocol: {cfg.protocol.toUpperCase()} ({cfg.idp_provider})
            </Badge>
            <Badge>Pinned Regional Cell: {cfg.region_cell}</Badge>
            <Badge>
              Cross-Region Egress:{" "}
              {cfg.cross_region_egress_blocked ? "BLOCKED" : "OPEN"}
            </Badge>
            <Badge>
              Attestation: {cfg.residency_attestation_sha256.slice(0, 14)}…
            </Badge>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Configure Enterprise SSO Federation & Regional Isolation Cell</h3>
        <ErrorNotice error={saveMutation.error} />
        <div className="form-grid">
          <Field label="Federation Protocol">
            <select
              value={protocol}
              onChange={(e) => setProtocol(e.target.value as "oidc" | "saml2")}
            >
              <option value="oidc">OpenID Connect (OIDC + PKCE)</option>
              <option value="saml2">SAML 2.0 Enterprise Assertion</option>
            </select>
          </Field>
          <Field label="Identity Provider (IdP)">
            <select
              value={idpProvider}
              onChange={(e) =>
                setIdpProvider(e.target.value as typeof idpProvider)
              }
            >
              <option value="okta">Okta Workforce Identity</option>
              <option value="entra_id">Microsoft Entra ID (Azure AD)</option>
              <option value="google_workspace">
                Google Workspace Enterprise
              </option>
              <option value="onelogin">OneLogin Enterprise</option>
            </select>
          </Field>
          <Field label="IdP Issuer URL / Entity ID">
            <input
              value={issuerUrl}
              onChange={(e) => setIssuerUrl(e.target.value)}
            />
          </Field>
          <Field label="SSO Client ID / Audience URI">
            <input
              value={ssoClientId}
              onChange={(e) => setSsoClientId(e.target.value)}
            />
          </Field>
          <Field label="Client Secret or X.509 Signing Certificate (Encrypted)">
            <input
              type="password"
              value={ssoSecret}
              onChange={(e) => setSsoSecret(e.target.value)}
              placeholder="Fernet-encrypted under tenant DEK"
            />
          </Field>
          <Field label="Verified Enterprise Email Domain">
            <input
              value={emailDomain}
              onChange={(e) => setEmailDomain(e.target.value)}
            />
          </Field>
          <Field label="Pinned Regional Data-Residency Cell">
            <select
              value={regionCell}
              onChange={(e) =>
                setRegionCell(e.target.value as typeof regionCell)
              }
            >
              <option value="us-east-va-cell-1">
                US East (N. Virginia · us-east-va-cell-1)
              </option>
              <option value="eu-central-fra-cell-1">
                EU Central (Frankfurt GDPR · eu-central-fra-cell-1)
              </option>
              <option value="me-central-dxb-cell-1">
                Middle East (Dubai DIFC · me-central-dxb-cell-1)
              </option>
              <option value="ap-south-khi-cell-1">
                South Asia (Karachi Sovereign · ap-south-khi-cell-1)
              </option>
            </select>
          </Field>
        </div>
        <Button
          busy={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          Save Enterprise SSO & Pin Regional Cell
        </Button>
        {ssoNotice && (
          <p role="status" className="success-text">
            {ssoNotice}
          </p>
        )}
      </div>
    </div>
  );
}

export function P3AutomationHub({
  initialTab = "reminders",
}: {
  initialTab?:
    | "reminders"
    | "portals"
    | "payments"
    | "sla"
    | "payouts"
    | "voice"
    | "encryption"
    | "ai-benchmarks"
    | "accounting-expansion"
    | "enterprise-sso";
}) {
  const [tab, setTab] = useState<string>(initialTab);
  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);
  const handleTabChange = (nextTab: string) => {
    setTab(nextTab);
    if (typeof window !== "undefined") {
      const tabRoutes: Record<string, string> = {
        reminders: "/reminders",
        portals: "/supplier-portals",
        payments: "/supplier-payments",
        sla: "/reconciliation-sla",
        payouts: "/payout-reconciliation",
        voice: "/voice-instructions",
        encryption: "/encryption-keys",
        "ai-benchmarks": "/ai-benchmarks",
        "accounting-expansion": "/accounting-expansion",
        "enterprise-sso": "/enterprise-sso",
      };
      const targetPath = tabRoutes[nextTab];
      if (
        targetPath &&
        Object.values(tabRoutes).includes(window.location.pathname)
      ) {
        window.history.replaceState(null, "", targetPath);
      }
    }
  };
  return (
    <>
      <PageHeading
        eyebrow="P3 AUTOMATION & CONTROLLED EXECUTION"
        title="Reminders, Portals, Payments, SLA, Payouts, Voice & Encryption"
        description="Multichannel WhatsApp & Slack reminders, 2FA TOTP supplier portals, human-approved supplier payments, 1-day reconciliation SLA, gross-to-net payout splits, voice-note instructions, and per-tenant envelope encryption."
      />
      <SectionTabs
        id="p3-automation"
        label="P3 Automation sections"
        value={tab}
        onChange={handleTabChange}
        items={[
          ["reminders", "C · WhatsApp & Slack Reminders"],
          ["portals", "D · Supplier Portal Connectors (2FA TOTP)"],
          ["payments", "E · Human-Approved Supplier Payments"],
          ["sla", "3.1 · 1-Day Reconciliation SLA"],
          ["payouts", "3.2 · Bank Feed & Gross-to-Net"],
          ["voice", "3.3 · Voice-Note Accounting"],
          ["encryption", "3.4 · Envelope Encryption & Shred"],
          ["ai-benchmarks", "P3-F · Multilingual OCR & AI Cost"],
          ["accounting-expansion", "P3-I · Revenue, Payroll & Short-Pay"],
          ["enterprise-sso", "P3-K · Enterprise SSO & Cell"],
        ]}
      />
      {tab === "reminders" && <MultichannelReminders />}
      {tab === "portals" && <SupplierPortalConnectors />}
      {tab === "payments" && <HumanApprovedSupplierPayments />}
      {tab === "sla" && <ReconciliationSlaDashboard />}
      {tab === "payouts" && <PayoutReconciliationHub />}
      {tab === "voice" && <VoiceNoteInstructionReview />}
      {tab === "encryption" && <TenantEncryptionKeyVault />}
      {tab === "ai-benchmarks" && <AiOcrCostBenchmarkHub />}
      {tab === "accounting-expansion" && <AccountingExpansionHub />}
      {tab === "enterprise-sso" && <EnterpriseSsoCellHub />}
    </>
  );
}
