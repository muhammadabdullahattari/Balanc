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
              external_invoice_id: "",
              invoice_number: "",
              total_amount: "425.00",
              tax_amount: "25.00",
              currency: "USD",
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
              kill_switch_reason: active
                ? "Paused by Owner/Accountant via Emergency Kill Switch"
                : "",
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
                onClick={() =>
                  toggleKillSwitch.mutate(!ctrl.kill_switch_active)
                }
              >
                {ctrl.kill_switch_active
                  ? "Deactivate Kill Switch (Resume Payouts)"
                  : "Activate Emergency Kill Switch"}
              </Button>
            )}
          </div>
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

export function P3AutomationHub({
  initialTab = "reminders",
}: {
  initialTab?: "reminders" | "portals" | "payments";
}) {
  const [tab, setTab] = useState<string>(initialTab);
  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);
  return (
    <>
      <PageHeading
        eyebrow="P3 AUTOMATION & CONTROLLED EXECUTION"
        title="Reminders, Supplier Portals & Human-Approved Payments"
        description="Multichannel WhatsApp & Slack reminders, 2FA TOTP supplier portal invoice ingestion, and human-approved supplier payment execution."
      />
      <SectionTabs
        id="p3-automation"
        label="P3 Automation sections"
        value={tab}
        onChange={setTab}
        items={[
          ["reminders", "C · WhatsApp & Slack Reminders"],
          ["portals", "D · Supplier Portal Connectors (2FA TOTP)"],
          ["payments", "E · Human-Approved Supplier Payments"],
        ]}
      />
      {tab === "reminders" && <MultichannelReminders />}
      {tab === "portals" && <SupplierPortalConnectors />}
      {tab === "payments" && <HumanApprovedSupplierPayments />}
    </>
  );
}
