import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { TimezoneSelect } from "../components/timezone-select";
import {
  AUTOMATION_PAGE_CONFIG,
  type AutomationPageMeta,
  type AutomationTabId,
} from "./automation-routes";

export {
  AUTOMATION_PAGE_CONFIG,
  type AutomationPageMeta,
  type AutomationTabId,
};
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

function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const text = value.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
}

function channelName(value: string | null | undefined): string {
  const key = (value || "").toLowerCase();
  if (key === "whatsapp") return "WhatsApp";
  if (key === "slack") return "Slack";
  if (key === "email") return "Email";
  if (key === "auto") return "Preferred channel";
  return humanize(value);
}

function railLabel(value: string | null | undefined): string {
  const key = (value || "").toLowerCase();
  if (key === "stripe_live") return "Stripe payout";
  if (key === "ach_nacha") return "US bank transfer (ACH)";
  if (key === "swift_wire") return "International wire";
  return humanize(value);
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

const REGION_LABELS: Record<string, string> = {
  "us-east-va-cell-1": "United States (Virginia)",
  "eu-central-fra-cell-1": "Europe (Frankfurt)",
  "me-central-dxb-cell-1": "Middle East (Dubai)",
  "ap-south-khi-cell-1": "South Asia (Karachi)",
};

function regionName(cellId: string | null | undefined): string {
  if (!cellId) return "Default region";
  return REGION_LABELS[cellId] || humanize(cellId);
}

const LEDGER_AREA_LABELS: Record<string, string> = {
  "app.journal_lines": "Journal lines",
  "app.journals": "Journal entries",
  "app.trade_documents": "Invoices and bills",
  "app.bank_rows": "Bank transactions",
};

function ledgerAreaName(raw: string): string {
  return LEDGER_AREA_LABELS[raw] || humanize(raw);
}

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
  open_documents?: Array<{
    id: string;
    kind: "bill" | "invoice";
    number: string;
    party_name: string;
    outstanding_amount: string;
    currency: string;
    due_date: string;
  }>;
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
  open_bills?: Array<{
    id: string;
    source: "ledger_bill" | "portal_ingestion";
    bill_reference: string;
    supplier_name: string;
    amount: string;
    currency: string;
    due_date: string;
  }>;
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

export function MultichannelReminders({
  channelFilter = "all",
}: {
  channelFilter?: "all" | "whatsapp" | "slack";
} = {}) {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [targetKind, setTargetKind] = useState<
    "bill" | "invoice" | "evidence" | "approval"
  >("bill");
  const [selectedDocId, setSelectedDocId] = useState<string>("");
  const [targetLabel, setTargetLabel] = useState("");
  const [recipientKey, setRecipientKey] = useState("ap-finance-owner");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [channel, setChannel] = useState<
    "auto" | "whatsapp" | "slack" | "email"
  >(channelFilter === "slack" ? "slack" : "whatsapp");
  const [customMessage, setCustomMessage] = useState("");
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
            ...(selectedDocId ? { target_id: selectedDocId } : {}),
            target_label: targetLabel,
            recipient_key: recipientKey,
            recipient_email: recipientEmail.trim(),
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
        `Reminder sent on ${channelName(res.channel)} to ${res.destination}. Status: ${humanize(res.status)}.`,
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
            recipient_name: String(fd.get("recipient_name") || "Finance team"),
            whatsapp_phone: String(fd.get("whatsapp_phone") || ""),
            slack_channel: String(fd.get("slack_channel") || ""),
            email_address: String(fd.get("email_address") || ""),
            whatsapp_opt_in: fd.get("whatsapp_opt_in") === "on",
            slack_opt_in: fd.get("slack_opt_in") === "on",
            email_fallback: true,
            quiet_start_hour: Number(fd.get("quiet_start_hour") || 22),
            quiet_end_hour: Number(fd.get("quiet_end_hour") || 7),
            timezone: String(fd.get("timezone") || b.timezone || "UTC"),
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
  const [whatsappNotice, setWhatsappNotice] = useState<string>("");
  const [slackNotice, setSlackNotice] = useState<string>("");

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
          "/api/v1/businesses/{business_id}/p3/reminders/{reminder_id}/delivery-status",
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
      setLastResult(`Reminder updated. Status: ${humanize(r.status)}.`);
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
    onSuccess: (_res, vars) => {
      const isWhatsapp = Boolean(
        vars.disconnect_whatsapp ||
        vars.whatsapp_phone_number_id ||
        vars.whatsapp_business_account_id ||
        vars.whatsapp_access_token ||
        vars.default_whatsapp_recipient !== undefined,
      );
      const message = vars.disconnect_whatsapp
        ? "WhatsApp has been disconnected."
        : vars.disconnect_slack
          ? "Slack has been disconnected."
          : isWhatsapp
            ? "WhatsApp connection saved. You can now send reminders on WhatsApp."
            : "Slack connection saved. You can now send reminders on Slack.";
      if (isWhatsapp) setWhatsappNotice(message);
      else setSlackNotice(message);
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-reminders"],
      });
    },
  });

  const whatsappConnected =
    cs?.whatsapp_mode === "workspace_custom" ||
    Boolean(cs?.whatsapp_cloud_connected);
  const slackConnected =
    cs?.slack_mode === "workspace_custom" || Boolean(cs?.slack_connected);
  const connectVars = connectChannelsMutation.variables;
  const whatsappError =
    connectVars &&
    (connectVars.disconnect_whatsapp ||
      connectVars.whatsapp_phone_number_id ||
      connectVars.whatsapp_access_token ||
      connectVars.whatsapp_business_account_id ||
      connectVars.default_whatsapp_recipient !== undefined)
      ? connectChannelsMutation.error
      : null;
  const slackError =
    connectVars &&
    (connectVars.disconnect_slack ||
      connectVars.slack_webhook_url ||
      connectVars.slack_bot_token ||
      connectVars.slack_workspace_name ||
      connectVars.default_slack_channel !== undefined)
      ? connectChannelsMutation.error
      : null;

  const showWhatsapp = channelFilter === "all" || channelFilter === "whatsapp";
  const showSlack = channelFilter === "all" || channelFilter === "slack";

  return (
    <div className="stack">
      <div className="panel">
        <h2>
          {channelFilter === "whatsapp"
            ? "WhatsApp notifications"
            : channelFilter === "slack"
              ? "Slack notifications"
              : "Reminders"}
        </h2>
        <p className="muted">
          {channelFilter === "whatsapp"
            ? "Connect your WhatsApp Business account independently to send document requests and payment reminders."
            : channelFilter === "slack"
              ? "Connect your Slack workspace independently to post finance reminders and approval alerts to your team."
              : "Connect WhatsApp and Slack separately below, then send reminders for missing receipts, overdue bills, or pending approvals. Email backup is always enabled."}
        </p>
        <div className="p3-badge-row">
          {showWhatsapp && (
            <Badge tone={whatsappConnected ? "good" : "neutral"}>
              WhatsApp: {whatsappConnected ? "Connected" : "Not connected yet"}
            </Badge>
          )}
          {showSlack && (
            <Badge tone={slackConnected ? "good" : "neutral"}>
              Slack: {slackConnected ? "Connected" : "Not connected yet"}
            </Badge>
          )}
          <Badge>Email backup: Always on</Badge>
        </div>
      </div>

      {showWhatsapp && (
        <div className="panel">
          <h3>Connect WhatsApp</h3>
          <p className="muted">
            Link your own WhatsApp Business number so reminders come from your
            business. Your access token is stored securely and never shown
            again.
          </p>
          <ol className="muted">
            <li>Open your WhatsApp Business account in Meta Business Suite.</li>
            <li>Copy the phone number ID, account ID and access token.</li>
            <li>Paste them below and choose who should receive reminders.</li>
          </ol>
          <ErrorNotice error={whatsappError} />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              setWhatsappNotice("");
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
              });
            }}
          >
            <div className="form-grid">
              <Field label="Phone number ID">
                <input
                  name="whatsapp_phone_number_id"
                  placeholder={
                    cs?.whatsapp_phone_number_id || "e.g. 1391722684032246"
                  }
                />
              </Field>
              <Field label="Business account ID">
                <input
                  name="whatsapp_business_account_id"
                  placeholder={
                    cs?.whatsapp_business_account_id || "e.g. 1877048859962524"
                  }
                />
              </Field>
              <Field
                label={`Access token ${cs?.whatsapp_token_masked ? `(saved: ${cs.whatsapp_token_masked})` : ""}`}
              >
                <input
                  name="whatsapp_access_token"
                  type="password"
                  placeholder="Paste your access token"
                />
              </Field>
              <Field label="Default recipient (phone number)">
                <input
                  name="default_whatsapp_recipient"
                  defaultValue={
                    cs?.whatsapp_mode === "workspace_custom"
                      ? cs?.default_whatsapp_recipient || ""
                      : ""
                  }
                  placeholder="e.g. +15550192834"
                />
              </Field>
            </div>
            <div className="p3-actions-row">
              <Button type="submit" busy={connectChannelsMutation.isPending}>
                Save WhatsApp connection
              </Button>
              {cs?.whatsapp_mode === "workspace_custom" && (
                <Button
                  type="button"
                  variant="secondary"
                  busy={connectChannelsMutation.isPending}
                  onClick={() => {
                    setWhatsappNotice("");
                    connectChannelsMutation.mutate({
                      disconnect_whatsapp: true,
                    });
                  }}
                >
                  Disconnect WhatsApp
                </Button>
              )}
            </div>
            {whatsappNotice && (
              <p
                role="status"
                className="success-text"
                style={{ marginTop: "8px" }}
              >
                {whatsappNotice}
              </p>
            )}
          </form>
        </div>
      )}

      {showSlack && (
        <div className="panel">
          <h3>Connect Slack</h3>
          <p className="muted">
            Post reminders to a Slack channel your team already uses. Your
            webhook link is stored securely and never shown again.
          </p>
          <ol className="muted">
            <li>
              In Slack, open your workspace's app settings and add an incoming
              webhook.
            </li>
            <li>
              Pick the channel it should post to and copy the webhook link.
            </li>
            <li>Paste the link below and save.</li>
          </ol>
          <ErrorNotice error={slackError} />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              setSlackNotice("");
              connectChannelsMutation.mutate({
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
              <Field label="Workspace name">
                <input
                  name="slack_workspace_name"
                  defaultValue={cs?.slack_workspace_name || ""}
                  placeholder="e.g. Acme Finance"
                />
              </Field>
              <Field
                label={`Incoming webhook link ${cs?.slack_credentials_masked ? `(saved: ${cs.slack_credentials_masked})` : ""}`}
              >
                <input
                  name="slack_webhook_url"
                  type="password"
                  placeholder="Paste your Slack webhook link"
                />
              </Field>
              <Field label="Bot token (optional)">
                <input
                  name="slack_bot_token"
                  type="password"
                  placeholder="Only needed if you are not using a webhook link"
                />
              </Field>
              <Field label="Default channel">
                <input
                  name="default_slack_channel"
                  defaultValue={
                    cs?.slack_mode === "workspace_custom"
                      ? cs?.default_slack_channel || ""
                      : ""
                  }
                  placeholder="e.g. #finance-alerts"
                />
              </Field>
            </div>
            <div className="p3-actions-row">
              <Button type="submit" busy={connectChannelsMutation.isPending}>
                Save Slack connection
              </Button>
              {cs?.slack_mode === "workspace_custom" && (
                <Button
                  type="button"
                  variant="secondary"
                  busy={connectChannelsMutation.isPending}
                  onClick={() => {
                    setSlackNotice("");
                    connectChannelsMutation.mutate({ disconnect_slack: true });
                  }}
                >
                  Disconnect Slack
                </Button>
              )}
            </div>
            {slackNotice && (
              <p
                role="status"
                className="success-text"
                style={{ marginTop: "8px" }}
              >
                {slackNotice}
              </p>
            )}
          </form>
        </div>
      )}

      <div className="panel">
        <h3>Send a reminder</h3>
        <p className="muted">
          Pick an open bill or invoice (or describe what you are chasing), then
          choose where to send the reminder.
        </p>
        <ErrorNotice error={dispatchMutation.error} />
        <div className="form-grid">
          <Field label="Open bill or invoice (optional)">
            <select
              value={selectedDocId}
              onChange={(e) => {
                const docId = e.target.value;
                setSelectedDocId(docId);
                const doc = (query.data?.open_documents || []).find(
                  (d) => d.id === docId,
                );
                if (doc) {
                  const nextKind = doc.kind === "invoice" ? "invoice" : "bill";
                  setTargetKind(nextKind);
                  const lbl = `${doc.party_name} ${humanize(doc.kind)} #${doc.number} (${doc.currency} ${doc.outstanding_amount} · Due ${doc.due_date})`;
                  setTargetLabel(lbl);
                  setCustomMessage(
                    `Balanc reminder: ${lbl} is awaiting review, payment, or supporting documents.`,
                  );
                }
              }}
            >
              <option value="">Enter details manually</option>
              {(query.data?.open_documents || []).map((doc) => (
                <option key={doc.id} value={doc.id}>
                  {humanize(doc.kind)} #{doc.number} — {doc.party_name} (
                  {doc.currency} {doc.outstanding_amount} · Due {doc.due_date})
                </option>
              ))}
            </select>
          </Field>
          <Field label="What are you chasing?">
            <select
              value={targetKind}
              onChange={(e) =>
                setTargetKind(
                  e.target.value as
                    "bill" | "invoice" | "evidence" | "approval",
                )
              }
            >
              <option value="bill">Overdue supplier bill</option>
              <option value="evidence">Missing receipt or document</option>
              <option value="approval">Pending payment approval</option>
              <option value="invoice">Customer invoice follow-up</option>
            </select>
          </Field>
          <Field label="Contact reference">
            <input
              value={recipientKey}
              onChange={(e) => setRecipientKey(e.target.value)}
              placeholder="Saved contact below, e.g. ap-finance-owner"
            />
          </Field>
          <Field label="Short description">
            <input
              value={targetLabel}
              onChange={(e) => setTargetLabel(e.target.value)}
              placeholder="e.g. Hosting bill #INV-114 ($800, due in 2 days)"
            />
          </Field>
          <Field label="Send via">
            <select
              value={channel}
              onChange={(e) =>
                setChannel(
                  e.target.value as "auto" | "whatsapp" | "slack" | "email",
                )
              }
            >
              <option value="whatsapp">WhatsApp</option>
              <option value="slack">
                Slack
                {cs?.default_slack_channel
                  ? ` (${cs.default_slack_channel})`
                  : ""}
              </option>
              <option value="email">Email</option>
              <option value="auto">Contact's preferred order</option>
            </select>
          </Field>
          <Field label="Recipient email (for Email delivery)">
            <input
              type="email"
              value={recipientEmail}
              onChange={(e) => setRecipientEmail(e.target.value)}
              placeholder="e.g. billing@client.com"
            />
          </Field>
        </div>
        <Field label="Message">
          <textarea
            rows={2}
            value={customMessage}
            onChange={(e) => setCustomMessage(e.target.value)}
            placeholder="e.g. Hi, a bill for $800 is waiting for your approval and receipt."
          />
        </Field>
        <div className="p3-actions-row">
          <Button
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate("whatsapp")}
          >
            Send on WhatsApp
          </Button>
          <Button
            variant="secondary"
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate("slack")}
          >
            Send on Slack
          </Button>
          <Button
            variant="secondary"
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate("email")}
          >
            Send by email
          </Button>
          <Button
            variant="secondary"
            busy={dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate(undefined)}
          >
            Send via selected channel
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
        <h3>Who to remind and when</h3>
        <p className="muted">
          Save a contact, their WhatsApp number or Slack channel, and the hours
          when we should not message them.
        </p>
        <ErrorNotice error={prefMutation.error} />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            prefMutation.mutate(e.currentTarget);
          }}
        >
          <div className="form-grid">
            <Field label="Contact reference">
              <input
                name="recipient_key"
                defaultValue="ap-finance-owner"
                required
              />
            </Field>
            <Field label="Contact name">
              <input
                name="recipient_name"
                placeholder="e.g. Accounts team"
                required
              />
            </Field>
            <Field label="WhatsApp number">
              <input
                name="whatsapp_phone"
                defaultValue={
                  cs?.whatsapp_mode === "workspace_custom"
                    ? cs?.default_whatsapp_recipient || ""
                    : ""
                }
                placeholder="e.g. +15550192834"
              />
            </Field>
            <Field label="Slack channel">
              <input
                name="slack_channel"
                defaultValue={
                  cs?.slack_mode === "workspace_custom"
                    ? cs?.default_slack_channel || ""
                    : ""
                }
                placeholder="e.g. #finance-alerts"
              />
            </Field>
            <Field label="Email address">
              <input
                name="email_address"
                type="email"
                placeholder="e.g. ap@vendor.com"
              />
            </Field>
            <TimezoneSelect
              name="timezone"
              label="Contact timezone"
              defaultValue={b.timezone}
            />
            <Field label="Quiet hours start (0-23)">
              <input
                name="quiet_start_hour"
                type="number"
                min={0}
                max={23}
                defaultValue={22}
              />
            </Field>
            <Field label="Quiet hours end (0-23)">
              <input
                name="quiet_end_hour"
                type="number"
                min={0}
                max={23}
                defaultValue={7}
              />
            </Field>
            <Field label="Maximum reminders per item">
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
              <input name="whatsapp_opt_in" type="checkbox" defaultChecked /> OK
              to message on WhatsApp
            </label>
            <label>
              <input name="slack_opt_in" type="checkbox" defaultChecked /> OK to
              message on Slack
            </label>
            <label>
              <input name="opted_out" type="checkbox" /> Do not send any
              reminders to this contact
            </label>
          </div>
          <Button busy={prefMutation.isPending}>Save contact</Button>
        </form>
      </div>

      {query.data && (
        <div className="panel">
          <h3>Reminder history</h3>
          <p className="muted">
            Every reminder we sent, and whether it was delivered.
          </p>
          <Table
            caption="Reminders sent"
            rows={query.data.reminders}
            rowKey={(r) => r.id}
            columns={[
              { label: "About", render: (r) => r.target_label },
              {
                label: "Channel",
                render: (r) => <Badge>{channelName(r.channel)}</Badge>,
              },
              { label: "Sent to", render: (r) => r.destination },
              {
                label: "Attempt",
                render: (r) => `#${r.attempt_number}`,
              },
              {
                label: "Status",
                render: (r) => <Badge>{humanize(r.status)}</Badge>,
              },
              {
                label: "Delivery updates",
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
                          Mark as delivered
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
                        {r.channel === "slack"
                          ? "Mark as acknowledged"
                          : "Mark as read"}
                      </Button>
                    </div>
                  ) : (
                    <Badge>{humanize(r.status)}</Badge>
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
  const [vendorName, setVendorName] = useState("");
  const [portalKind, setPortalKind] = useState<
    | "web_totp"
    | "api_stripe"
    | "utility_portal"
    | "telecom_portal"
    | "cloud_portal"
  >("web_totp");
  const [portalUrl, setPortalUrl] = useState("");
  const [loginUsername, setLoginUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totpSeed, setTotpSeed] = useState("");
  const [liveTotpInfo, setLiveTotpInfo] = useState<{
    code: string;
    remaining: number;
  } | null>(null);
  const [syncInvoiceNumber, setSyncInvoiceNumber] = useState("");
  const [syncTotalAmount, setSyncTotalAmount] = useState("");
  const [syncTaxAmount, setSyncTaxAmount] = useState("");
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
        `Signed in to the supplier portal and imported invoice ${res.invoice_number} (${res.currency} ${res.total_amount}) as a draft bill for your review. Status: ${humanize(res.status)}.`,
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
        `Added ${res.supplier_name} invoice ${res.bill_reference} (${res.currency} ${res.amount}) to your supplier payments as a draft. Status: ${humanize(res.state)}.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-supplier-payments"],
      });
    },
  });

  return (
    <div className="stack">
      <div className="panel">
        <h2>Supplier portals</h2>
        <p className="muted">
          Connect your suppliers' billing portals (utilities, telecom, software
          and cloud providers) so their invoices are fetched automatically and
          turned into draft bills for you to review. Your logins are stored
          securely.
        </p>
      </div>

      <div className="panel">
        <h3>Connect a supplier portal</h3>
        <p className="muted">
          Enter the login of a view-only billing user at your supplier. If the
          portal asks for a two-step verification code, add its setup key too.
          Tip: use a quick fill below to start.
        </p>
        <div
          className="p3-actions-row"
          style={{ marginTop: 0, marginBottom: "14px" }}
        >
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setVendorName("Amazon Web Services (AWS Cloud Billing)");
              setPortalKind("cloud_portal");
              setPortalUrl(
                "https://console.aws.amazon.com/billing/home#/bills",
              );
            }}
          >
            Quick fill: Cloud hosting
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setVendorName("K-Electric Corporate Utility Portal");
              setPortalKind("utility_portal");
              setPortalUrl("https://billing.ke.com.pk/corporate/invoices");
            }}
          >
            Quick fill: Utility provider
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setVendorName("Stripe Vendor Billing API");
              setPortalKind("api_stripe");
              setPortalUrl("https://api.stripe.com/v1/invoices");
            }}
          >
            Quick fill: Stripe invoices
          </Button>
        </div>
        <ErrorNotice
          error={createPortalMutation.error || generateSeedMutation.error}
        />
        <div className="form-grid">
          <Field label="Supplier name">
            {" "}
            <input
              value={vendorName}
              onChange={(e) => setVendorName(e.target.value)}
              placeholder="e.g. City Power & Water"
            />
          </Field>
          <Field label="Type of portal">
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
                Website login with two-step verification
              </option>
              <option value="utility_portal">
                Utility provider (electricity, gas, water)
              </option>
              <option value="telecom_portal">
                Telecom provider (phone, internet)
              </option>
              <option value="cloud_portal">Cloud or software provider</option>
              <option value="api_stripe">
                Stripe invoices (direct connection)
              </option>
            </select>
          </Field>
          <Field label="Portal login web address">
            {" "}
            <input
              value={portalUrl}
              onChange={(e) => setPortalUrl(e.target.value)}
              placeholder="e.g. https://billing.yoursupplier.com"
            />
          </Field>
          <Field label="Login email or username">
            {" "}
            <input
              value={loginUsername}
              onChange={(e) => setLoginUsername(e.target.value)}
              placeholder="e.g. billing-viewer@yourcompany.com"
            />
          </Field>
          <Field label="Password (stored securely)">
            {" "}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password for that login"
            />
          </Field>
          <Field label="Two-step verification setup key (optional)">
            <input
              value={totpSeed}
              onChange={(e) => setTotpSeed(e.target.value.toUpperCase())}
              placeholder="Shown as 'Can't scan the barcode?' in your supplier's portal"
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
            Save connection
          </Button>
          <Button
            variant="secondary"
            busy={generateSeedMutation.isPending}
            onClick={() => generateSeedMutation.mutate()}
          >
            Generate a setup key
          </Button>
          {liveTotpInfo && (
            <Badge>
              Current verification code: {liveTotpInfo.code} (valid for{" "}
              {liveTotpInfo.remaining}s)
            </Badge>
          )}
        </div>
      </div>

      {query.data && (
        <div className="panel">
          <h3>Your connected portals</h3>
          <p className="muted">
            Click <strong>Fetch latest invoice</strong> on any active portal
            below. Balanc signs in for you, reads the newest invoice and creates
            a draft bill for you to review.
          </p>
          <ErrorNotice
            error={syncPortalMutation.error || revokeMutation.error}
          />
          <details className="p3-advanced-details">
            <summary>Advanced options: invoice number and amounts</summary>
            <div className="form-grid">
              <Field label="Invoice number (optional)">
                <input
                  value={syncInvoiceNumber}
                  onChange={(e) => setSyncInvoiceNumber(e.target.value)}
                  placeholder="Leave blank to detect automatically, or e.g. INV-2026-991"
                />
              </Field>
              <Field label="Invoice total (optional)">
                {" "}
                <input
                  type="number"
                  step="0.01"
                  value={syncTotalAmount}
                  onChange={(e) => setSyncTotalAmount(e.target.value)}
                  placeholder="e.g. 425.00"
                />
              </Field>
              <Field label="Tax amount (optional)">
                {" "}
                <input
                  type="number"
                  step="0.01"
                  value={syncTaxAmount}
                  onChange={(e) => setSyncTaxAmount(e.target.value)}
                  placeholder="e.g. 25.00"
                />
              </Field>
            </div>
          </details>
          {syncMessage && (
            <p role="status" className="success-text">
              {syncMessage}
            </p>
          )}
          <Table
            caption="Connected supplier portals"
            rows={query.data.connections}
            rowKey={(c) => c.id}
            columns={[
              { label: "Supplier", render: (c) => c.vendor_name },
              {
                label: "Type",
                render: (c) => <Badge>{humanize(c.portal_kind)}</Badge>,
              },
              { label: "Login", render: (c) => c.login_username },
              {
                label: "Two-step verification",
                render: (c) =>
                  c.totp_enabled ? (
                    <Badge>
                      {" "}
                      Enabled
                      {c.live_totp_preview
                        ? ` · code ${c.live_totp_preview}`
                        : ""}{" "}
                    </Badge>
                  ) : (
                    "Direct connection"
                  ),
              },
              {
                label: "Status",
                render: (c) => <Badge>{humanize(c.status)}</Badge>,
              },
              {
                label: "Actions",
                render: (c) => (
                  <div style={{ display: "flex", gap: "8px" }}>
                    <Button
                      disabled={c.status !== "active"}
                      busy={syncPortalMutation.isPending}
                      onClick={() => syncPortalMutation.mutate(c.id)}
                    >
                      Fetch latest invoice
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
          title="Disconnect supplier portal"
          description="Stop automatic invoice fetching and remove the stored login for this supplier."
          onClose={() => setRevokeTarget(null)}
        >
          <p>
            Are you sure you want to disconnect{" "}
            <strong>{revokeTarget.vendor_name}</strong>? Automatic invoice
            fetching will stop and the stored login will be removed right away.
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
              Disconnect portal
            </Button>
          </div>
        </Modal>
      )}

      {query.data && (
        <div className="panel">
          <h3>Imported supplier invoices</h3>{" "}
          <p className="muted">
            {" "}
            Invoices fetched from your supplier portals. Review each one, then
            send it to supplier payments.{" "}
          </p>
          <Table
            caption="Imported invoices waiting for your review"
            rows={query.data.ingestions}
            rowKey={(i) => i.id}
            columns={[
              { label: "Supplier", render: (i) => i.vendor_name },
              { label: "Invoice #", render: (i) => i.invoice_number },
              {
                label: "Amount (Tax)",
                render: (i) =>
                  `${i.currency} ${i.total_amount} (tax ${i.tax_amount})`,
              },
              {
                label: "Reading accuracy",
                render: (i) => `${(i.ocr_confidence * 100).toFixed(1)}%`,
              },
              {
                label: "Status",
                render: (i) => <Badge>{humanize(i.status)}</Badge>,
              },
              {
                label: "Draft bill",
                render: (i) => (i.trade_draft_id ? "Created" : "—"),
              },
              {
                label: "Next step",
                render: (i) => (
                  <Button
                    variant="secondary"
                    busy={promoteToPaymentMutation.isPending}
                    onClick={() => promoteToPaymentMutation.mutate(i.id)}
                  >
                    Send to supplier payments
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

  const [supplierName, setSupplierName] = useState("");
  const [holderName, setHolderName] = useState("");
  const [bankName, setBankName] = useState("");
  const [routing, setRouting] = useState("");
  const [accountNumber, setAccountNumber] = useState("");
  const [rail, setRail] = useState<"stripe_live" | "ach_nacha" | "swift_wire">(
    "stripe_live",
  );

  const [selectedBeneficiaryId, setSelectedBeneficiaryId] = useState("");
  const [billRef, setBillRef] = useState("");
  const [payAmount, setPayAmount] = useState("");
  const [stepUpCode, setStepUpCode] = useState("");
  const [activeRemittance, setActiveRemittance] = useState<string>("");
  const [statusNotice, setStatusNotice] = useState<string>("");
  const [confirmPayment, setConfirmPayment] = useState<
    PaymentsData["payments"][number] | null
  >(null);
  const [killSwitchModalOpen, setKillSwitchModalOpen] = useState(false);
  const [killSwitchReason, setKillSwitchReason] = useState("");

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
        `Payment draft saved. ${res.requires_dual_approval ? "Two people need to approve it." : "It needs one approval."} Find it in the approval list below.`,
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
          `Payment approved. Status: ${humanize(res.state)}.${res.provider_reference ? ` Reference: ${res.provider_reference}.` : ""}`,
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
        <h2>Supplier payments</h2>
        <p className="muted">
          Prepare supplier payments, then have an owner or accountant approve
          them. Nothing is paid until a person approves it with a verification
          code. Start by adding the supplier's bank details, then prepare a
          payment.
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
              Payments: {ctrl.kill_switch_active ? "Paused" : "Active"}
            </Badge>
            <Badge>Single payment limit: ${ctrl.single_payment_limit}</Badge>
            <Badge>
              Two approvals needed above: ${ctrl.dual_approval_threshold}
            </Badge>
            <Badge>
              Stripe:{" "}
              {ctrl.stripe_live_available ? "Connected" : "Not connected yet"}
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
                  ? "Resume payments"
                  : "Pause all payments"}
              </Button>
            )}
          </div>
        )}
        {killSwitchModalOpen && (
          <Modal
            open={true}
            title="Pause all supplier payments"
            description="Immediately stop all outgoing supplier payments for this business until you resume them."
            onClose={() => setKillSwitchModalOpen(false)}
          >
            <div className="stack" style={{ gap: "12px" }}>
              <Field label="Reason for pausing">
                {" "}
                <input
                  value={killSwitchReason}
                  onChange={(e) => setKillSwitchReason(e.target.value)}
                  placeholder="e.g. Suspicious request, checking with the bank"
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
                  Pause all payments
                </Button>
              </div>
            </div>
          </Modal>
        )}
      </div>

      <div className="panel">
        <h3>Add a supplier's bank details</h3>{" "}
        <p className="muted">
          {" "}
          Save the bank account you will pay. Confirm the details with the
          supplier directly (for example by phone) before saving.{" "}
        </p>
        <ErrorNotice error={createBeneficiary.error} />
        <div className="form-grid">
          <Field label="Supplier name">
            {" "}
            <input
              value={supplierName}
              onChange={(e) => setSupplierName(e.target.value)}
              placeholder="e.g. Acme Supplies Ltd"
            />
          </Field>
          <Field label="Account holder name">
            {" "}
            <input
              value={holderName}
              onChange={(e) => setHolderName(e.target.value)}
              placeholder="Name on the bank account"
            />
          </Field>
          <Field label="Bank name">
            {" "}
            <input
              value={bankName}
              onChange={(e) => setBankName(e.target.value)}
              placeholder="e.g. First National Bank"
            />
          </Field>
          <Field label="Bank routing number or international bank code">
            {" "}
            <input
              value={routing}
              onChange={(e) => setRouting(e.target.value)}
              placeholder="Routing number or SWIFT code"
            />
          </Field>
          <Field label="Account number">
            {" "}
            <input
              value={accountNumber}
              onChange={(e) => setAccountNumber(e.target.value)}
              placeholder="Account number or IBAN"
            />
          </Field>
          <Field label="How you will pay">
            <select
              value={rail}
              onChange={(e) =>
                setRail(
                  e.target.value as "stripe_live" | "ach_nacha" | "swift_wire",
                )
              }
            >
              <option value="stripe_live">Stripe payout</option>
              <option value="ach_nacha">US bank transfer (ACH)</option>
              <option value="swift_wire">International wire</option>
            </select>
          </Field>
        </div>
        <Button
          busy={createBeneficiary.isPending}
          onClick={() => createBeneficiary.mutate()}
        >
          Save bank details
        </Button>
      </div>

      <div className="panel">
        <h3>Prepare a payment</h3>
        <p className="muted">
          Choose an open supplier bill (or type a reference), pick the
          supplier's saved bank details and enter the amount. The payment is
          only a draft until someone approves it.
        </p>
        <ErrorNotice error={preparePayment.error} />
        <div className="form-grid">
          <Field label="Open supplier bill (optional)">
            <select
              defaultValue=""
              onChange={(e) => {
                const chosen = (query.data?.open_bills || []).find(
                  (ob) => `${ob.source}:${ob.id}` === e.target.value,
                );
                if (chosen) {
                  setBillRef(chosen.bill_reference);
                  setPayAmount(chosen.amount);
                  const matchedBen = (query.data?.beneficiaries || []).find(
                    (ben) =>
                      ben.supplier_name.toLowerCase() ===
                      chosen.supplier_name.toLowerCase(),
                  );
                  if (matchedBen) {
                    setSelectedBeneficiaryId(matchedBen.id);
                  }
                }
              }}
            >
              <option value="">Enter a reference manually</option>
              {(query.data?.open_bills || []).map((ob) => (
                <option
                  key={`${ob.source}:${ob.id}`}
                  value={`${ob.source}:${ob.id}`}
                >
                  [{ob.source === "ledger_bill" ? "Bill" : "Portal invoice"}]{" "}
                  {ob.bill_reference} — {ob.supplier_name} ({ob.currency}{" "}
                  {ob.amount})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Supplier bank details">
            <select
              value={
                selectedBeneficiaryId || query.data?.beneficiaries[0]?.id || ""
              }
              onChange={(e) => setSelectedBeneficiaryId(e.target.value)}
            >
              {!query.data?.beneficiaries.length && (
                <option value="">
                  No saved bank details yet. Add them above.
                </option>
              )}
              {(query.data?.beneficiaries || []).map((ben) => (
                <option key={ben.id} value={ben.id}>
                  {ben.supplier_name} — {ben.bank_name} ({" "}
                  {ben.account_number_masked})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Bill or invoice reference">
            {" "}
            <input
              value={billRef}
              onChange={(e) => setBillRef(e.target.value)}
              placeholder="e.g. INV-2026-114"
            />
          </Field>
          <Field label="Amount">
            {" "}
            <input
              type="number"
              step="0.01"
              value={payAmount}
              onChange={(e) => setPayAmount(e.target.value)}
              placeholder="e.g. 425.00"
            />
          </Field>
          <Field label="Payment method">
            <select
              value={rail}
              onChange={(e) =>
                setRail(
                  e.target.value as "stripe_live" | "ach_nacha" | "swift_wire",
                )
              }
            >
              <option value="stripe_live">Stripe payout</option>
              <option value="ach_nacha">US bank transfer (ACH)</option>
              <option value="swift_wire">International wire</option>
            </select>
          </Field>
        </div>
        <Button
          disabled={!query.data?.beneficiaries.length}
          busy={preparePayment.isPending}
          onClick={() => preparePayment.mutate()}
        >
          Prepare payment draft
        </Button>
      </div>

      {query.data && (
        <div className="panel">
          <h3>Approve payments</h3>
          <p className="muted">
            {" "}
            Payments wait here until an owner or accountant approves them. Enter
            your approval code to release a payment.{" "}
          </p>{" "}
          <ErrorNotice error={approveAndExecute.error} />
          {statusNotice && (
            <p role="status" className="success-text">
              {statusNotice}
            </p>
          )}
          <div style={{ maxWidth: "360px", marginBottom: "14px" }}>
            <Field label="Approval code (from your authenticator app)">
              <input
                value={stepUpCode}
                onChange={(e) => setStepUpCode(e.target.value)}
                placeholder="6-digit code"
              />
            </Field>
          </div>
          <Table
            caption="Supplier payments"
            rows={query.data.payments}
            rowKey={(p) => p.id}
            columns={[
              { label: "Supplier", render: (p) => p.supplier_name },
              { label: "Bill reference", render: (p) => p.bill_reference },
              {
                label: "Amount",
                render: (p) => `${p.currency} ${p.amount}`,
              },
              {
                label: "Method",
                render: (p) => <Badge>{railLabel(p.rail)}</Badge>,
              },
              {
                label: "Status",
                render: (p) => <Badge>{humanize(p.state)}</Badge>,
              },
              {
                label: "Reference",
                render: (p) =>
                  p.provider_reference ||
                  p.failure_reason ||
                  "Awaiting approval",
              },
              {
                label: "Recorded in your books",
                render: (p) => (p.settlement_journal_id ? "Yes" : "—"),
              },
              {
                label: "Action",
                render: (p) => (
                  <div style={{ display: "flex", gap: "8px" }}>
                    {(p.state === "prepared" || p.state === "first_approved") &&
                      canApprove && (
                        <Button
                          busy={approveAndExecute.isPending}
                          onClick={() => setConfirmPayment(p)}
                        >
                          {p.state === "first_approved"
                            ? "Give final approval and pay"
                            : "Approve and pay"}
                        </Button>
                      )}
                    {p.remittance_advice && (
                      <Button
                        variant="secondary"
                        onClick={() => setActiveRemittance(p.remittance_advice)}
                      >
                        View payment advice
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
              title="Approve supplier payment"
              description="Check the bank details below and enter your approval code to release the payment."
              onClose={() => setConfirmPayment(null)}
            >
              <div className="stack" style={{ gap: "12px" }}>
                <p className="muted">
                  Check the bank details and enter your approval code before
                  releasing the payment.
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
                    <strong>Bill reference:</strong>{" "}
                    {confirmPayment.bill_reference}
                  </div>
                  <div>
                    <strong>Amount:</strong> {confirmPayment.currency}{" "}
                    {confirmPayment.amount}
                  </div>
                  <div>
                    <strong>Payment method:</strong>{" "}
                    <Badge>{railLabel(confirmPayment.rail)}</Badge>
                  </div>
                  <div>
                    <strong>Bank account:</strong>{" "}
                    <code>
                      {query.data.beneficiaries.find(
                        (ben) => ben.id === confirmPayment.beneficiary_id,
                      )?.account_number_masked || "Verified account"}
                    </code>
                  </div>
                </div>
                <Field label="Approval code (from your authenticator app)">
                  <input
                    value={stepUpCode}
                    onChange={(e) => setStepUpCode(e.target.value)}
                    placeholder="6-digit code"
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
                    Approve payment
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
              <h4>Payment advice</h4>
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
      setChaseNotice(`Countdown started: ${slaHours} hours to finish.`);
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
        `Sent ${res.chased_count} reminder(s) via ${channelName(chaseChannel)}. ${res.total_blockers} item(s) still open.`,
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
        <h2>Daily reconciliation</h2>
        <p className="muted">
          Track how close you are to finishing your daily reconciliation:
          unchecked bank transactions, bills missing receipts, drafts waiting
          for review and month-end tasks. One click sends reminders for anything
          missing.
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
            <Badge>Status: {humanize(t.status)}</Badge>
            <Badge>
              Time left: {hrsLeft}h {minsLeft}m (goal: {t.sla_hours}h)
            </Badge>
            <Badge>Complete: {t.completion_percent}%</Badge>
            <Badge>Open items: {t.total_blockers}</Badge>
            <Badge>Reminders sent: {t.chased_count}</Badge>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Set your goal and send reminders</h3>{" "}
        <p className="muted">
          {" "}
          Choose how many hours you want to finish in, then remind people about
          anything still open.{" "}
        </p>
        <ErrorNotice
          error={startSlaMutation.error || autoChaseMutation.error}
        />
        <div className="form-grid">
          <Field label="Goal (hours to finish)">
            <input
              type="number"
              min={1}
              max={168}
              value={slaHours}
              onChange={(e) => setSlaHours(Number(e.target.value) || 24)}
            />
          </Field>
          <Field label="Send reminders via">
            <select
              value={chaseChannel}
              onChange={(e) =>
                setChaseChannel(
                  e.target.value as "auto" | "whatsapp" | "slack" | "email",
                )
              }
            >
              <option value="auto">
                Automatic (WhatsApp, then Slack, then email)
              </option>
              <option value="whatsapp">WhatsApp</option>
              <option value="slack">Slack</option>
              <option value="email">Email</option>
            </select>
          </Field>
        </div>
        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
          <Button
            busy={autoChaseMutation.isPending}
            onClick={() => autoChaseMutation.mutate()}
          >
            Remind about open items
          </Button>
          <Button
            variant="secondary"
            busy={startSlaMutation.isPending}
            onClick={() => startSlaMutation.mutate()}
          >
            Start {slaHours}-hour countdown
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
          <h3>Reconciliation checklist</h3>
          <div
            style={{
              height: "10px",
              borderRadius: "999px",
              background: "var(--surface-alt)",
              border: "1px solid var(--line)",
              overflow: "hidden",
              marginBottom: "14px",
            }}
            aria-label={`Reconciliation ${t.completion_percent}% complete`}
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
            caption="Reconciliation checklist"
            rows={t.checklist}
            rowKey={(c) => c.key}
            columns={[
              { label: "Task", render: (c) => c.label },
              {
                label: "Open items",
                render: (c) => <strong>{c.open_count}</strong>,
              },
              {
                label: "Status",
                render: (c) => (
                  <Badge>{c.completed ? "Done" : "Needs action"}</Badge>
                ),
              },
            ]}
          />
          {t.unlinked_bills.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h4>Supplier bills missing a receipt</h4>
              <Table
                caption="Supplier bills missing a receipt"
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
              <h4>Bank transactions not yet reviewed</h4>
              <Table
                caption="Bank transactions not yet reviewed"
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
  const [payoutRef, setPayoutRef] = useState("");
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
        `Imported ${res.imported_count} bank transaction(s) (${res.skipped_duplicates} duplicate(s) skipped).`,
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
              payout_reference:
                payoutRef || `PAYOUT-${new Date().toISOString().slice(0, 10)}`,
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
        `Saved payout breakdown ${res.payout_reference}: gross sales ${res.gross_sales_amount}, net bank deposit ${res.net_payout_amount}.`,
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
        <h2>Bank feed &amp; merchant payouts</h2>
        <p className="muted">
          Import recent bank feed transactions and split merchant deposits
          (Stripe, Shopify, Amazon, Square) into gross sales, processing fees,
          refunds, withheld tax, and the net deposit that hit your bank account.
        </p>
        <div className="p3-actions-row">
          <Badge
            tone={
              query.data?.feed_status.stripe_financial_connections_live
                ? "good"
                : "neutral"
            }
          >
            Bank feed:{" "}
            {query.data?.feed_status.stripe_financial_connections_live
              ? "Live connection"
              : "Ready"}
          </Badge>
          <Button
            variant="secondary"
            busy={syncFeedMutation.isPending}
            onClick={() => syncFeedMutation.mutate()}
          >
            Import latest bank transactions
          </Button>
        </div>
      </div>

      <div className="panel">
        <h3>Split a merchant payout</h3>
        <p className="muted">
          Enter the figures from your payment processor statement to create a
          balanced entry matching your bank deposit.
        </p>
        <ErrorNotice
          error={syncFeedMutation.error || reconcileMutation.error}
        />
        <div className="form-grid">
          <Field label="Payment platform">
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
              <option value="stripe_financial_connections">Stripe</option>
              <option value="shopify_payouts">Shopify Payments</option>
              <option value="amazon_settlement">Amazon Seller</option>
              <option value="square_payouts">Square</option>
            </select>
          </Field>
          <Field label="Payout reference">
            <input
              value={payoutRef}
              onChange={(e) => setPayoutRef(e.target.value)}
              placeholder="e.g. PO-2026-1126"
            />
          </Field>
          <Field label="Gross sales">
            <input
              type="number"
              step="0.01"
              value={grossSales}
              onChange={(e) => setGrossSales(e.target.value)}
            />
          </Field>
          <Field label="Processing fees">
            <input
              type="number"
              step="0.01"
              value={processingFee}
              onChange={(e) => setProcessingFee(e.target.value)}
            />
          </Field>
          <Field label="Customer refunds & disputes">
            <input
              type="number"
              step="0.01"
              value={refundAmount}
              onChange={(e) => setRefundAmount(e.target.value)}
            />
          </Field>
          <Field label="Marketplace tax withheld">
            <input
              type="number"
              step="0.01"
              value={taxWithheld}
              onChange={(e) => setTaxWithheld(e.target.value)}
            />
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            busy={reconcileMutation.isPending}
            onClick={() => reconcileMutation.mutate()}
          >
            Save payout split
          </Button>
          <Badge>
            Expected bank deposit: {b.currency || "USD"} {netPreview}
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
          <h3>Saved payout splits</h3>
          <Table
            caption="Saved merchant payout splits"
            emptyText="No merchant payouts split yet."
            rows={query.data.reconciliations}
            rowKey={(r) => r.id}
            columns={[
              { label: "Reference", render: (r) => r.payout_reference },
              {
                label: "Platform",
                render: (r) => <Badge>{humanize(r.provider)}</Badge>,
              },
              {
                label: "Gross sales",
                render: (r) => `${r.currency} ${r.gross_sales_amount}`,
              },
              {
                label: "Fees / refunds / tax",
                render: (r) =>
                  `-${r.processing_fee_amount} / -${r.refund_dispute_amount} / -${r.tax_withheld_amount}`,
              },
              {
                label: "Net bank deposit",
                render: (r) => (
                  <strong>
                    {r.currency} {r.net_payout_amount}
                  </strong>
                ),
              },
              {
                label: "Status",
                render: (r) => <Badge>{humanize(r.status)}</Badge>,
              },
            ]}
          />
          {query.data.recent_bank_feed_rows.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h4>Recently imported bank transactions</h4>
              <Table
                caption="Recently imported bank transactions"
                rows={query.data.recent_bank_feed_rows}
                rowKey={(row) => row.id}
                columns={[
                  { label: "Date", render: (row) => row.accounting_date },
                  { label: "Description", render: (row) => row.description },
                  { label: "Amount", render: (row) => row.amount },
                  {
                    label: "Status",
                    render: (row) => <Badge>{humanize(row.state)}</Badge>,
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

  const [audioFilename, setAudioFilename] = useState("");
  const [transcript, setTranscript] = useState("");
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
            audio_filename: audioFilename || "voice-note.webm",
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
        `Prepared a ${humanize(res.extracted_action).toLowerCase()} draft for ${res.extracted_counterparty} (${res.extracted_currency} ${res.extracted_amount}). Review it below.`,
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
                  ? "Approved during review"
                  : "Declined during review",
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
        <h2>Voice notes</h2>
        <p className="muted">
          Upload a voice memo or type a quick spoken note to turn bills,
          expenses, and invoices into drafts for your accountant to review.
        </p>
      </div>

      <div className="panel">
        <h3>Create a draft from a voice note</h3>
        <ErrorNotice error={ingestMutation.error || reviewMutation.error} />
        <div className="form-grid">
          <Field label="Recording title (optional)">
            <input
              value={audioFilename}
              onChange={(e) => setAudioFilename(e.target.value)}
              placeholder="e.g. november-electricity-bill.webm"
            />
          </Field>
          <Field label="Upload audio file (.webm, .mp3, .wav)">
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
        <div className="p3-actions-row" style={{ marginBottom: "12px" }}>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setAudioFilename("hosting-bill.webm");
              setTranscript(
                "Record supplier bill from Cloud Hosting Co for $480.00 including $30.00 tax for November hosting.",
              );
            }}
          >
            Try example: Supplier bill
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              setAudioFilename("client-invoice.webm");
              setTranscript(
                "Issue customer invoice to Acme Corp for $3500.00 including $250.00 tax for advisory work.",
              );
            }}
          >
            Try example: Sales invoice
          </Button>
        </div>
        <Field label="Spoken note or transcript">
          <textarea
            rows={2}
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder="Describe the bill, expense, or invoice in plain words…"
          />
        </Field>
        <div className="p3-actions-row">
          <Button
            disabled={!transcript.trim()}
            busy={ingestMutation.isPending}
            onClick={() => ingestMutation.mutate()}
          >
            Create draft for review
          </Button>
        </div>
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
          <h3>Voice notes awaiting review</h3>
          <Table
            caption="Voice notes awaiting review"
            emptyText="No voice notes submitted yet."
            rows={query.data.instructions}
            rowKey={(i) => i.id}
            columns={[
              {
                label: "Type",
                render: (i) => <Badge>{humanize(i.extracted_action)}</Badge>,
              },
              {
                label: "Contact",
                render: (i) => i.extracted_counterparty,
              },
              {
                label: "Amount (tax)",
                render: (i) =>
                  `${i.extracted_currency} ${i.extracted_amount} (tax ${i.extracted_tax_amount})`,
              },
              {
                label: "Note",
                render: (i) => i.transcript_text,
              },
              {
                label: "Confidence",
                render: (i) => `${Math.round(i.confidence_score * 100)}%`,
              },
              {
                label: "Status",
                render: (i) => <Badge>{humanize(i.status)}</Badge>,
              },
              {
                label: "Review",
                render: (i) =>
                  i.status === "pending_review" && canReview ? (
                    <div className="p4-inline-actions">
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
                        Decline
                      </Button>
                    </div>
                  ) : (
                    <span className="muted">{humanize(i.status)}</span>
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
  const [destroyReason, setDestroyReason] = useState("");
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
        `Created new workspace encryption key (version ${res.key_version}).`,
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
              reason: destroyReason || "Workspace closure request",
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
        `Permanently erased encryption key version ${res.destroyed_key_version} (${res.shredded_records_count} saved credential(s) rendered unreadable).`,
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
        <h2>Data encryption</h2>
        <p className="muted">
          Saved supplier logins, tokens, and sensitive workspace secrets are
          protected with a dedicated encryption key for this business. You can
          rotate the key periodically or permanently erase it when closing the
          workspace.
        </p>
        {es && (
          <div className="p3-actions-row">
            <Badge tone={es.master_kek_configured ? "good" : "neutral"}>
              Protection: {es.master_kek_configured ? "Active" : "Standard"}
            </Badge>
            <Badge>
              Current key:{" "}
              {es.active_key_version
                ? `Version ${es.active_key_version} (${humanize(es.active_key_status)})`
                : "Erased"}
            </Badge>
            <Badge>Protected items: {es.protected_records_count}</Badge>
            <Button
              variant="secondary"
              busy={rotateMutation.isPending}
              onClick={() => rotateMutation.mutate()}
            >
              Rotate encryption key
            </Button>
            {isOwner && !es.provably_shredded && (
              <Button
                variant="danger"
                onClick={() => setDestroyModalOpen(true)}
              >
                Permanently erase key
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
          <h3>Key history</h3>
          <Table
            caption="Encryption key history"
            emptyText="No key versions recorded yet."
            rows={query.data.keys}
            rowKey={(k) => k.id}
            columns={[
              { label: "Version", render: (k) => `Version ${k.key_version}` },
              {
                label: "Created",
                render: (k) => formatWhen(k.created_at),
              },
              {
                label: "Key state",
                render: (k) => (k.wrapped_dek_present ? "Active" : "Erased"),
              },
              {
                label: "Status",
                render: (k) => <Badge>{humanize(k.status)}</Badge>,
              },
              {
                label: "Erasure confirmation",
                render: (k) =>
                  k.destruction_certificate_sha256 ? "Certificate issued" : "—",
              },
            ]}
          />
        </div>
      )}

      {destroyModalOpen && (
        <Modal
          open={true}
          title="Permanently erase workspace encryption key"
          description="This permanently makes all stored connector passwords and tokens for this workspace unreadable. This cannot be undone."
          onClose={() => setDestroyModalOpen(false)}
        >
          <div className="stack" style={{ gap: "12px" }}>
            <Field label="Reason for erasing">
              <input
                value={destroyReason}
                onChange={(e) => setDestroyReason(e.target.value)}
                placeholder="e.g. Closing business workspace"
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
            <div className="p3-modal-actions">
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
                Erase key permanently
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
        `Sample scan check completed: ${(res.field_extraction_f1 * 100).toFixed(1)}% field accuracy (${res.abstained_to_manual_review ? "routed to manual review because scan was blurry" : "ready for draft"}).`,
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
        <h2>Document scanning quality</h2>
        <p className="muted">
          Preview how invoice and receipt scanning handles different languages,
          rotated phone photos, and blurry receipts. Unclear scans are sent to
          manual review rather than guessed.
        </p>
        {s && (
          <div className="p3-badge-row">
            <Badge>Sample figures</Badge>
            <Badge>
              Estimated cost per document: ${s.current_p3_cost_per_doc_usd}
            </Badge>
            <Badge>Estimated savings: {s.cost_reduction_percent}%</Badge>
          </div>
        )}
        <p className="muted" role="note" style={{ marginTop: "10px" }}>
          <strong>Note:</strong> The figures on this page use built-in sample
          receipts to test rotated and blurry scan handling — they are not
          measurements from your own uploaded documents.
        </p>
      </div>

      <div className="panel">
        <h3>Run a sample scan check</h3>
        <ErrorNotice error={evalMutation.error} />
        <div className="form-grid">
          <Field label="Document language">
            <select
              value={locale}
              onChange={(e) => setLocale(e.target.value as typeof locale)}
            >
              <option value="ur_PK">Urdu (Pakistan)</option>
              <option value="ar_AE">Arabic (UAE)</option>
              <option value="en_US">English (US)</option>
              <option value="es_ES">Spanish</option>
              <option value="fr_FR">French</option>
            </select>
          </Field>
          <Field label="Page angle or condition">
            <select
              value={orientation}
              onChange={(e) =>
                setOrientation(e.target.value as typeof orientation)
              }
            >
              <option value="90_deg_rotated">Rotated sideways (90°)</option>
              <option value="180_deg_upside_down">Upside down (180°)</option>
              <option value="270_deg_rotated">Rotated sideways (270°)</option>
              <option value="skewed_thermal_receipt">
                Tilted paper receipt
              </option>
              <option value="0_deg_clean">Straight PDF or scan</option>
            </select>
          </Field>
          <Field label="Scanning method">
            <select
              value={routingTier}
              onChange={(e) =>
                setRoutingTier(e.target.value as typeof routingTier)
              }
            >
              <option value="hybrid_rule_cache_local_ocr">
                Standard fast scanner (recommended)
              </option>
              <option value="deterministic_template_cache">
                Known supplier layout
              </option>
              <option value="cloud_vlm_fallback">
                Detailed visual reader for complex pages
              </option>
            </select>
          </Field>
        </div>
        <div className="p3-actions-row">
          <label className="p4-check-row">
            <input
              type="checkbox"
              checked={simulateBlur}
              onChange={(e) => setSimulateBlur(e.target.checked)}
            />
            <span>Test a blurry photo (verifies manual-review safeguard)</span>
          </label>
          <Button
            busy={evalMutation.isPending}
            onClick={() => evalMutation.mutate()}
          >
            Run sample check
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
          <h3>Recent sample checks</h3>
          <Table
            caption="Sample document scan checks"
            emptyText="No sample checks run yet."
            rows={query.data.runs}
            rowKey={(r) => r.id}
            columns={[
              {
                label: "Language",
                render: (r) => <Badge>{r.language_locale}</Badge>,
              },
              {
                label: "Condition",
                render: (r) => humanize(r.scan_orientation),
              },
              {
                label: "Reading accuracy",
                render: (r) =>
                  `${(r.ocr_char_accuracy * 100).toFixed(1)}% text · ${(r.field_extraction_f1 * 100).toFixed(1)}% fields`,
              },
              {
                label: "Outcome",
                render: (r) => (
                  <Badge
                    tone={r.abstained_to_manual_review ? "warning" : "good"}
                  >
                    {r.abstained_to_manual_review
                      ? "Sent to manual review"
                      : "Ready for draft"}
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
  const [reference, setReference] = useState("");
  const [counterparty, setCounterparty] = useState("");
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
          "/api/v1/businesses/{business_id}/p3/accounting-expansion/compute",
          {
            params: { path },
            body: {
              workflow_kind: workflowKind,
              reference: reference || "SCHED-01",
              counterparty_name: counterparty || "Customer / Payroll",
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
        `Prepared ${humanize(res.workflow_kind).toLowerCase()} (${res.reference}): ${res.currency} ${res.recognized_or_settled_amount} recognized, ${res.currency} ${res.deferred_or_variance_amount} remaining/variance.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p3-accounting-expansion"],
      });
    },
  });

  return (
    <div className="stack">
      <div className="panel">
        <h2>Revenue schedules, payroll &amp; short payments</h2>
        <p className="muted">
          Spread multi-month contract revenue evenly across months, split gross
          payroll into wages, taxes, and benefits, or resolve invoices where a
          customer paid slightly less than billed.
        </p>
      </div>

      <div className="panel">
        <h3>Prepare a schedule or split</h3>
        <ErrorNotice error={executeMutation.error} />
        <div className="form-grid">
          <Field label="What would you like to calculate?">
            <select
              value={workflowKind}
              onChange={(e) =>
                setWorkflowKind(e.target.value as typeof workflowKind)
              }
            >
              <option value="contract_revenue_schedule">
                Monthly contract revenue schedule
              </option>
              <option value="payroll_clearing_split">
                Payroll wages, taxes &amp; benefits split
              </option>
              <option value="short_pay_resolution">
                Customer short-payment resolution
              </option>
            </select>
          </Field>
          <Field label="Reference">
            <input
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder="e.g. ANNUAL-PLAN-104 or NOV-PAYROLL"
            />
          </Field>
          <Field label="Customer or batch name">
            <input
              value={counterparty}
              onChange={(e) => setCounterparty(e.target.value)}
              placeholder="e.g. Acme Corp or November Staff Payroll"
            />
          </Field>
          <Field label="Total amount">
            <input
              type="number"
              step="0.01"
              value={totalAmount}
              onChange={(e) => setTotalAmount(e.target.value)}
            />
          </Field>
          {workflowKind === "contract_revenue_schedule" && (
            <>
              <Field label="Contract length (months)">
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={months}
                  onChange={(e) => setMonths(Number(e.target.value) || 12)}
                />
              </Field>
              <Field label="Month number to record">
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
              <Field label="Employer payroll taxes">
                <input
                  type="number"
                  step="0.01"
                  value={employerTax}
                  onChange={(e) => setEmployerTax(e.target.value)}
                />
              </Field>
              <Field label="Employee tax withheld">
                <input
                  type="number"
                  step="0.01"
                  value={employeeWithholding}
                  onChange={(e) => setEmployeeWithholding(e.target.value)}
                />
              </Field>
              <Field label="Employee benefits deduction">
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
              <Field label="Amount actually received">
                <input
                  type="number"
                  step="0.01"
                  value={settledAmount}
                  onChange={(e) => setSettledAmount(e.target.value)}
                />
              </Field>
              <Field label="Reason for the difference">
                <select
                  value={resolutionPolicy}
                  onChange={(e) =>
                    setResolutionPolicy(
                      e.target.value as typeof resolutionPolicy,
                    )
                  }
                >
                  <option value="early_payment_discount">
                    Early-payment discount
                  </option>
                  <option value="bank_fee_writeoff">
                    Bank wire fee deducted
                  </option>
                  <option value="disputed_credit_memo">
                    Agreed credit or allowance
                  </option>
                </select>
              </Field>
            </>
          )}
        </div>
        <div className="p3-actions-row">
          <Button
            busy={executeMutation.isPending}
            onClick={() => executeMutation.mutate()}
          >
            Calculate &amp; create draft
          </Button>
        </div>
        {expNotice && (
          <p role="status" className="success-text">
            {expNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Saved schedules &amp; splits</h3>
          <Table
            caption="Saved revenue, payroll, and short-payment entries"
            emptyText="No schedules or splits created yet."
            rows={query.data.workflows}
            rowKey={(w) => w.id}
            columns={[
              {
                label: "Type",
                render: (w) => <Badge>{humanize(w.workflow_kind)}</Badge>,
              },
              { label: "Reference", render: (w) => w.reference },
              {
                label: "Total",
                render: (w) => `${w.currency} ${w.total_amount}`,
              },
              {
                label: "Recorded now",
                render: (w) =>
                  `${w.currency} ${w.recognized_or_settled_amount}`,
              },
              {
                label: "Deferred / difference",
                render: (w) => `${w.currency} ${w.deferred_or_variance_amount}`,
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
  const [issuerUrl, setIssuerUrl] = useState("");
  const [ssoClientId, setSsoClientId] = useState("");
  const [ssoSecret, setSsoSecret] = useState("");
  const [emailDomain, setEmailDomain] = useState("");
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
            issuer_url: issuerUrl || "https://login.yourcompany.com",
            sso_client_id: ssoClientId || "company-sso-client",
            sso_client_secret_or_cert: ssoSecret,
            email_domain: emailDomain || "yourcompany.com",
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
        `Single sign-on settings saved. Workspace data region set to ${regionName(res.region_cell)}.`,
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
        <h2>Single sign-on &amp; data region</h2>
        <p className="muted">
          Let your team sign in with your company identity provider (Okta,
          Microsoft, Google Workspace, or OneLogin) and choose the geographic
          region where your workspace data is stored.
        </p>
        {cfg && (
          <div className="p3-badge-row">
            <Badge>Provider: {humanize(cfg.idp_provider)}</Badge>
            <Badge>Data region: {regionName(cfg.region_cell)}</Badge>
            <Badge tone={cfg.cross_region_egress_blocked ? "good" : "neutral"}>
              Data stays in region:{" "}
              {cfg.cross_region_egress_blocked ? "Yes" : "Standard"}
            </Badge>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Configure company sign-in &amp; region</h3>
        <ErrorNotice error={saveMutation.error} />
        <div className="form-grid">
          <Field label="Identity provider">
            <select
              value={idpProvider}
              onChange={(e) =>
                setIdpProvider(e.target.value as typeof idpProvider)
              }
            >
              <option value="okta">Okta</option>
              <option value="entra_id">Microsoft Entra ID</option>
              <option value="google_workspace">Google Workspace</option>
              <option value="onelogin">OneLogin</option>
            </select>
          </Field>
          <Field label="Sign-in method">
            <select
              value={protocol}
              onChange={(e) => setProtocol(e.target.value as "oidc" | "saml2")}
            >
              <option value="oidc">OpenID Connect (recommended)</option>
              <option value="saml2">SAML 2.0</option>
            </select>
          </Field>
          <Field label="Provider sign-in URL">
            <input
              value={issuerUrl}
              onChange={(e) => setIssuerUrl(e.target.value)}
              placeholder="e.g. https://yourcompany.okta.com/oauth2/default"
            />
          </Field>
          <Field label="Application client ID">
            <input
              value={ssoClientId}
              onChange={(e) => setSsoClientId(e.target.value)}
              placeholder="Paste the client ID from your provider"
            />
          </Field>
          <Field label="Client secret or certificate">
            <input
              type="password"
              value={ssoSecret}
              onChange={(e) => setSsoSecret(e.target.value)}
              placeholder="Stored securely and never displayed again"
            />
          </Field>
          <Field label="Company email domain">
            <input
              value={emailDomain}
              onChange={(e) => setEmailDomain(e.target.value)}
              placeholder="e.g. yourcompany.com"
            />
          </Field>
          <Field label="Preferred data region">
            <select
              value={regionCell}
              onChange={(e) =>
                setRegionCell(e.target.value as typeof regionCell)
              }
            >
              <option value="us-east-va-cell-1">
                United States (Virginia)
              </option>
              <option value="eu-central-fra-cell-1">Europe (Frankfurt)</option>
              <option value="me-central-dxb-cell-1">Middle East (Dubai)</option>
              <option value="ap-south-khi-cell-1">South Asia (Karachi)</option>
            </select>
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            busy={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            Save sign-in &amp; region settings
          </Button>
        </div>
        {ssoNotice && (
          <p role="status" className="success-text">
            {ssoNotice}
          </p>
        )}
      </div>
    </div>
  );
}

export function P4EnterpriseOperationsHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [serviceTier, setServiceTier] = useState<
    "starter" | "growth" | "enterprise" | "sovereign"
  >("enterprise");
  const [dailyPageQuota, setDailyPageQuota] = useState("50000");
  const [monthlyAiBudget, setMonthlyAiBudget] = useState("500.00");
  const [storageQuotaGb, setStorageQuotaGb] = useState("250");
  const [maxConcurrentWorkers, setMaxConcurrentWorkers] = useState("16");
  const [fairShareWeight, setFairShareWeight] = useState("100");
  const [hardQuotaEnforcement, setHardQuotaEnforcement] = useState(true);
  const [alertThresholdPercent, setAlertThresholdPercent] = useState("80");
  const [runbookOwner, setRunbookOwner] = useState("");
  const [pagesCount, setPagesCount] = useState("1200");
  const [aiCostUsd, setAiCostUsd] = useState("14.40");
  const [storageMb, setStorageMb] = useState("240.00");
  const [simulateNoisyNeighbor, setSimulateNoisyNeighbor] = useState(false);
  const [opsNotice, setOpsNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p4-operations"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p4/operations", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        quotas: {
          service_tier: string;
          daily_page_quota: number;
          monthly_ai_budget_usd: string;
          storage_quota_gb: number;
          max_concurrent_workers: number;
          fair_share_weight: number;
          hard_quota_enforcement: boolean;
          alert_threshold_percent: number;
          runbook_owner: string;
          incident_state: string;
        };
        telemetry: {
          pages_ingested_today: number;
          page_utilization_percent: number;
          ai_spend_mtd_usd: string;
          ai_budget_remaining_usd: string;
          ai_budget_utilization_percent: number;
          storage_used_mb: string;
          alert_triggered: boolean;
          noisy_neighbor_isolation: string;
        };
      },
  });

  const saveQuotasMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p4/operations/quotas", {
          params: { path },
          body: {
            service_tier: serviceTier,
            daily_page_quota: Math.max(100, Number(dailyPageQuota) || 50000),
            monthly_ai_budget_usd: monthlyAiBudget || "500.00",
            storage_quota_gb: Math.max(1, Number(storageQuotaGb) || 250),
            max_concurrent_workers: Math.max(
              1,
              Number(maxConcurrentWorkers) || 16,
            ),
            fair_share_weight: Math.max(10, Number(fairShareWeight) || 100),
            hard_quota_enforcement: hardQuotaEnforcement,
            alert_threshold_percent: Math.min(
              99,
              Math.max(50, Number(alertThresholdPercent) || 80),
            ),
            runbook_owner: runbookOwner || "billing@yourcompany.com",
          },
        }),
      )) as unknown as {
        service_tier: string;
        daily_page_quota: number;
        monthly_ai_budget_usd: string;
        incident_state: string;
      },
    onSuccess: (res) => {
      setOpsNotice(
        `Usage limits saved: up to ${res.daily_page_quota.toLocaleString()} pages per day and $${res.monthly_ai_budget_usd} monthly AI budget.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-operations"],
      });
    },
  });

  const meterMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p4/operations/meter-workload",
          {
            params: { path },
            body: {
              pages_count: Math.max(1, Number(pagesCount) || 1200),
              ai_cost_usd: aiCostUsd || "0.00",
              storage_mb: storageMb || "0.00",
              simulate_noisy_neighbor: simulateNoisyNeighbor,
            },
          },
        ),
      )) as unknown as {
        pages_ingested_today: number;
        ai_spend_mtd_usd: string;
        storage_used_mb: string;
        incident_state: string;
        noisy_neighbor_throttled: boolean;
      },
    onSuccess: (res) => {
      setOpsNotice(
        `Usage updated: ${res.pages_ingested_today.toLocaleString()} pages processed today, $${res.ai_spend_mtd_usd} spent this month.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-operations"],
      });
    },
  });

  const q = query.data?.quotas;
  const tel = query.data?.telemetry;

  return (
    <div className="stack">
      <div className="panel">
        <h2>Usage limits &amp; spending alerts</h2>
        <p className="muted">
          Set daily document limits, storage allowances, and monthly AI spending
          caps for your workspace so there are never surprise charges.
        </p>
        {q && tel && (
          <>
            <div className="p4-kpi-grid">
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Plan &amp; status</span>
                <span className="p4-kpi-value">{humanize(q.service_tier)}</span>
                <span className="p4-kpi-sub">
                  <Badge
                    tone={q.incident_state === "healthy" ? "good" : "warning"}
                  >
                    {humanize(q.incident_state)}
                  </Badge>
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Pages processed today</span>
                <span className="p4-kpi-value">
                  {tel.pages_ingested_today.toLocaleString()} /{" "}
                  {q.daily_page_quota.toLocaleString()}
                </span>
                <span className="p4-kpi-sub">
                  {tel.page_utilization_percent}% of daily limit
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Monthly AI spend</span>
                <span className="p4-kpi-value">${tel.ai_spend_mtd_usd}</span>
                <span className="p4-kpi-sub">
                  ${tel.ai_budget_remaining_usd} left of $
                  {q.monthly_ai_budget_usd}
                </span>
              </div>
              <div className="p4-kpi-card">
                <span className="p4-kpi-label">Document storage</span>
                <span className="p4-kpi-value">{tel.storage_used_mb} MB</span>
                <span className="p4-kpi-sub">
                  Allowance: {q.storage_quota_gb} GB
                </span>
              </div>
            </div>
            <div
              className="p3-progress-track"
              style={{ marginTop: "14px", marginBottom: 0 }}
              aria-label="Daily page limit usage"
            >
              <div
                className="p3-progress-fill"
                style={{
                  width: `${Math.min(100, tel.page_utilization_percent)}%`,
                }}
              />
            </div>
          </>
        )}
      </div>

      <div className="panel">
        <h3>Update workspace limits</h3>
        <ErrorNotice error={query.error || saveQuotasMutation.error} />
        <div className="form-grid">
          <Field label="Plan tier">
            <select
              value={serviceTier}
              onChange={(e) =>
                setServiceTier(e.target.value as typeof serviceTier)
              }
            >
              <option value="starter">Starter</option>
              <option value="growth">Growth</option>
              <option value="enterprise">Enterprise</option>
              <option value="sovereign">Dedicated</option>
            </select>
          </Field>
          <Field label="Daily document page limit">
            <input
              type="number"
              min={100}
              value={dailyPageQuota}
              onChange={(e) => setDailyPageQuota(e.target.value)}
            />
          </Field>
          <Field label="Monthly AI spending limit (USD)">
            <input
              type="number"
              step="0.01"
              value={monthlyAiBudget}
              onChange={(e) => setMonthlyAiBudget(e.target.value)}
            />
          </Field>
          <Field label="Document storage limit (GB)">
            <input
              type="number"
              min={1}
              value={storageQuotaGb}
              onChange={(e) => setStorageQuotaGb(e.target.value)}
            />
          </Field>
          <Field label="Alert me when usage reaches (%)">
            <input
              type="number"
              min={50}
              max={99}
              value={alertThresholdPercent}
              onChange={(e) => setAlertThresholdPercent(e.target.value)}
            />
          </Field>
          <Field label="Alert notification email">
            <input
              value={runbookOwner}
              onChange={(e) => setRunbookOwner(e.target.value)}
              placeholder="e.g. finance@yourcompany.com"
            />
          </Field>
        </div>
        <details className="p3-advanced-details">
          <summary>Advanced processing options</summary>
          <div className="form-grid">
            <Field label="Parallel background tasks">
              <input
                type="number"
                min={1}
                max={256}
                value={maxConcurrentWorkers}
                onChange={(e) => setMaxConcurrentWorkers(e.target.value)}
              />
            </Field>
            <Field label="Processing priority (10–1000)">
              <input
                type="number"
                min={10}
                max={1000}
                value={fairShareWeight}
                onChange={(e) => setFairShareWeight(e.target.value)}
              />
            </Field>
          </div>
          <label className="p4-check-row" style={{ marginTop: "10px" }}>
            <input
              type="checkbox"
              checked={hardQuotaEnforcement}
              onChange={(e) => setHardQuotaEnforcement(e.target.checked)}
            />
            <span>
              Pause new background tasks automatically when limit is reached
            </span>
          </label>
        </details>
        <div className="p3-actions-row">
          <Button
            busy={saveQuotasMutation.isPending}
            onClick={() => saveQuotasMutation.mutate()}
          >
            Save limits
          </Button>
        </div>

        <details className="p3-advanced-details" style={{ marginTop: "18px" }}>
          <summary>Test limit alerts with sample usage</summary>
          <ErrorNotice error={meterMutation.error} />
          <div className="form-grid">
            <Field label="Sample pages">
              <input
                type="number"
                min={1}
                value={pagesCount}
                onChange={(e) => setPagesCount(e.target.value)}
              />
            </Field>
            <Field label="Sample AI spend (USD)">
              <input
                type="number"
                step="0.01"
                value={aiCostUsd}
                onChange={(e) => setAiCostUsd(e.target.value)}
              />
            </Field>
            <Field label="Sample storage (MB)">
              <input
                type="number"
                step="0.01"
                value={storageMb}
                onChange={(e) => setStorageMb(e.target.value)}
              />
            </Field>
          </div>
          <label className="p4-check-row" style={{ marginTop: "10px" }}>
            <input
              type="checkbox"
              checked={simulateNoisyNeighbor}
              onChange={(e) => setSimulateNoisyNeighbor(e.target.checked)}
            />
            <span>Test high-volume traffic spike protection</span>
          </label>
          <div className="p3-actions-row">
            <Button
              variant="secondary"
              busy={meterMutation.isPending}
              onClick={() => meterMutation.mutate()}
            >
              Run usage test
            </Button>
          </div>
        </details>

        {opsNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "12px" }}
          >
            {opsNotice}
          </p>
        )}
      </div>
    </div>
  );
}

export function P4OnlineLedgerMigrationsHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [migrationName, setMigrationName] = useState("");
  const [targetTable, setTargetTable] = useState<
    | "app.journal_lines"
    | "app.journals"
    | "app.trade_documents"
    | "app.bank_rows"
  >("app.journal_lines");
  const [pattern, setPattern] = useState<
    | "expand_backfill_verify_contract"
    | "online_index_concurrently"
    | "dual_read_write_backfill"
  >("expand_backfill_verify_contract");
  const [batchSize, setBatchSize] = useState("1000");
  const [lockTimeoutMs, setLockTimeoutMs] = useState("50");
  const [migNotice, setMigNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p4-online-migrations"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p4/online-migrations", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        live_ledger_invariants: {
          posted_journal_lines_count: number;
          total_debits: string;
          total_credits: string;
          trial_balance_balanced: boolean;
        };
        migrations: Array<{
          id: string;
          migration_name: string;
          target_table: string;
          pattern: string;
          phase: string;
          batch_size: number;
          lock_timeout_ms: number;
          total_rows: number;
          processed_rows: number;
          last_cursor: string;
          pre_backfill_debit_total: string;
          pre_backfill_credit_total: string;
          post_backfill_debit_total: string;
          post_backfill_credit_total: string;
          trial_balance_tied_out: boolean;
          availability_percent: number;
          checksum_sha256: string;
          created_at: string;
        }>;
      },
  });

  const runMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p4/online-migrations/run", {
          params: { path },
          body: {
            migration_name: migrationName || "ledger-balance-verification",
            target_table: targetTable,
            pattern,
            batch_size: Math.max(50, Number(batchSize) || 1000),
            lock_timeout_ms: Math.max(10, Number(lockTimeoutMs) || 50),
          },
        }),
      )) as unknown as {
        id: string;
        migration_name: string;
        phase: string;
        trial_balance_tied_out: boolean;
        checksum_sha256: string;
      },
    onSuccess: (res) => {
      setMigNotice(
        `Data update "${res.migration_name}" finished (${humanize(res.phase)}). Trial balance check: ${res.trial_balance_tied_out ? "Balanced" : "Needs attention"}.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-online-migrations"],
      });
    },
  });

  const transitionMutation = useMutation({
    mutationFn: async ({
      jobId,
      targetPhase,
    }: {
      jobId: string;
      targetPhase:
        | "paused"
        | "backfilling"
        | "verified_ready"
        | "completed_verified"
        | "rolled_back";
    }) =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p4/online-migrations/{job_id}/transition",
          {
            params: { path: { business_id: b.id, job_id: jobId } },
            body: { target_phase: targetPhase },
          },
        ),
      )) as unknown as { migration_name: string; phase: string },
    onSuccess: (res) => {
      setMigNotice(
        `Updated "${res.migration_name}" to ${humanize(res.phase)}.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-online-migrations"],
      });
    },
  });

  const inv = query.data?.live_ledger_invariants;

  return (
    <div className="stack">
      <div className="panel">
        <h2>Data migration &amp; balance check</h2>
        <p className="muted">
          Update large sets of historical accounting records without downtime,
          while automatically verifying that your debits and credits stay equal
          before and after every run.
        </p>
        {inv && (
          <div className="p4-kpi-grid">
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Posted ledger lines</span>
              <span className="p4-kpi-value">
                {inv.posted_journal_lines_count.toLocaleString()}
              </span>
              <span className="p4-kpi-sub">Saved in your books</span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Total debits</span>
              <span className="p4-kpi-value">
                {b.currency} {inv.total_debits}
              </span>
              <span className="p4-kpi-sub">Current trial balance</span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Total credits</span>
              <span className="p4-kpi-value">
                {b.currency} {inv.total_credits}
              </span>
              <span className="p4-kpi-sub">
                <Badge tone={inv.trial_balance_balanced ? "good" : "warning"}>
                  {inv.trial_balance_balanced ? "Balanced" : "Out of balance"}
                </Badge>
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Start a background data update</h3>
        <ErrorNotice
          error={query.error || runMutation.error || transitionMutation.error}
        />
        <div className="form-grid">
          <Field label="Update name">
            <input
              value={migrationName}
              onChange={(e) => setMigrationName(e.target.value)}
              placeholder="e.g. Q4 department tag update"
            />
          </Field>
          <Field label="Records to check">
            <select
              value={targetTable}
              onChange={(e) =>
                setTargetTable(e.target.value as typeof targetTable)
              }
            >
              <option value="app.journal_lines">Journal lines</option>
              <option value="app.journals">Journal entries</option>
              <option value="app.trade_documents">Invoices and bills</option>
              <option value="app.bank_rows">Bank transactions</option>
            </select>
          </Field>
          <Field label="Update method">
            <select
              value={pattern}
              onChange={(e) => setPattern(e.target.value as typeof pattern)}
            >
              <option value="expand_backfill_verify_contract">
                Safe step-by-step update &amp; verify (recommended)
              </option>
              <option value="online_index_concurrently">
                Refresh search index without downtime
              </option>
              <option value="dual_read_write_backfill">
                Parallel background copy
              </option>
            </select>
          </Field>
        </div>
        <details className="p3-advanced-details">
          <summary>Advanced batch settings</summary>
          <div className="form-grid">
            <Field label="Records per batch">
              <input
                type="number"
                min={50}
                max={50000}
                value={batchSize}
                onChange={(e) => setBatchSize(e.target.value)}
              />
            </Field>
            <Field label="Maximum wait time (ms)">
              <input
                type="number"
                min={10}
                max={5000}
                value={lockTimeoutMs}
                onChange={(e) => setLockTimeoutMs(e.target.value)}
              />
            </Field>
          </div>
        </details>
        <div className="p3-actions-row">
          <Button
            busy={runMutation.isPending}
            onClick={() => runMutation.mutate()}
          >
            Start update &amp; verify balances
          </Button>
        </div>
        {migNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "12px" }}
          >
            {migNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Update history</h3>
          <Table
            caption="Data migration history"
            emptyText="No background updates run yet."
            rows={query.data.migrations}
            rowKey={(m) => m.id}
            columns={[
              {
                label: "Update",
                render: (m) => (
                  <div>
                    <strong>{m.migration_name}</strong>
                    <div className="muted" style={{ fontSize: "0.78rem" }}>
                      {ledgerAreaName(m.target_table)}
                    </div>
                  </div>
                ),
              },
              {
                label: "Status",
                render: (m) => (
                  <Badge
                    tone={
                      m.phase === "completed_verified"
                        ? "good"
                        : m.phase === "rolled_back"
                          ? "warning"
                          : "neutral"
                    }
                  >
                    {humanize(m.phase)}
                  </Badge>
                ),
              },
              {
                label: "Records checked",
                render: (m) => `${m.processed_rows} / ${m.total_rows}`,
              },
              {
                label: "Balance check",
                render: (m) => (
                  <Badge tone={m.trial_balance_tied_out ? "good" : "warning"}>
                    {m.trial_balance_tied_out ? "Balanced" : "Mismatch"}
                  </Badge>
                ),
              },
              {
                label: "Actions",
                render: (m) => (
                  <div className="p4-inline-actions">
                    {m.phase !== "paused" && (
                      <Button
                        variant="ghost"
                        busy={transitionMutation.isPending}
                        onClick={() =>
                          transitionMutation.mutate({
                            jobId: m.id,
                            targetPhase: "paused",
                          })
                        }
                      >
                        Pause
                      </Button>
                    )}
                    {m.phase !== "completed_verified" && (
                      <Button
                        variant="secondary"
                        busy={transitionMutation.isPending}
                        onClick={() =>
                          transitionMutation.mutate({
                            jobId: m.id,
                            targetPhase: "completed_verified",
                          })
                        }
                      >
                        Mark complete
                      </Button>
                    )}
                    {m.phase !== "rolled_back" && (
                      <Button
                        variant="danger"
                        busy={transitionMutation.isPending}
                        onClick={() =>
                          transitionMutation.mutate({
                            jobId: m.id,
                            targetPhase: "rolled_back",
                          })
                        }
                      >
                        Undo
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function P4BulkIngestionHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [batchReference, setBatchReference] = useState("");
  const [workerLane, setWorkerLane] = useState<
    "live_priority" | "bulk_background"
  >("bulk_background");
  const [sourceChannel, setSourceChannel] = useState<
    | "archive_backfill_zip"
    | "erp_historical_pdfs"
    | "supplier_portal_bulk"
    | "multi_entity_box_import"
  >("archive_backfill_zip");
  const [totalPages, setTotalPages] = useState("2500");
  const [tenantConcurrencySlots, setTenantConcurrencySlots] = useState("8");
  const [bulkNotice, setBulkNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p4-bulk-ingestion"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p4/bulk-ingestion", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        cell_telemetry: {
          live_lane_status: string;
          bulk_lane_status: string;
          target_daily_capacity_pages: number;
          max_supported_backlog_pages: number;
          foreground_p95_slo_ms: number;
          observed_foreground_p95_ms: number;
          total_pages_processed_workspace: number;
        };
        batches: Array<{
          id: string;
          batch_reference: string;
          worker_lane: string;
          source_channel: string;
          total_pages: number;
          processed_pages: number;
          failed_pages: number;
          tenant_concurrency_slots: number;
          throughput_pages_per_sec: number;
          foreground_p95_latency_ms: number;
          foreground_p99_latency_ms: number;
          backpressure_status: string;
          unit_cost_per_page_usd: string;
          status: string;
          created_at: string;
        }>;
      },
  });

  const submitMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p4/bulk-ingestion/submit", {
          params: { path },
          body: {
            batch_reference: batchReference || "ARCHIVE-IMPORT",
            worker_lane: workerLane,
            source_channel: sourceChannel,
            total_pages: Math.max(1, Number(totalPages) || 2500),
            tenant_concurrency_slots: Math.min(
              64,
              Math.max(1, Number(tenantConcurrencySlots) || 8),
            ),
          },
        }),
      )) as unknown as {
        batch_reference: string;
        processed_pages: number;
        total_pages: number;
        backpressure_status: string;
        foreground_p95_latency_ms: number;
        status: string;
      },
    onSuccess: (res) => {
      setBulkNotice(
        `Started bulk import "${res.batch_reference}": ${res.processed_pages.toLocaleString()} of ${res.total_pages.toLocaleString()} pages queued.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-bulk-ingestion"],
      });
    },
  });

  const cancelMutation = useMutation({
    mutationFn: async (batchId: string) =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p4/bulk-ingestion/{batch_id}/cancel",
          {
            params: { path: { business_id: b.id, batch_id: batchId } },
          },
        ),
      )) as unknown as { batch_reference: string; status: string },
    onSuccess: (res) => {
      setBulkNotice(`Cancelled import batch "${res.batch_reference}".`);
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-bulk-ingestion"],
      });
    },
  });

  const cell = query.data?.cell_telemetry;

  return (
    <div className="stack">
      <div className="panel">
        <h2>Bulk document import</h2>
        <p className="muted">
          Import large historical archives of PDFs, invoices, and receipts in
          the background without slowing down everyday work for your team.
        </p>
        {cell && (
          <div className="p4-kpi-grid">
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Background importer</span>
              <span className="p4-kpi-value">
                {humanize(cell.bulk_lane_status)}
              </span>
              <span className="p4-kpi-sub">
                Everyday app speed: Fast ({cell.observed_foreground_p95_ms} ms)
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Daily capacity</span>
              <span className="p4-kpi-value">
                {cell.target_daily_capacity_pages.toLocaleString()} pages
              </span>
              <span className="p4-kpi-sub">Runs in the background</span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Pages imported so far</span>
              <span className="p4-kpi-value">
                {cell.total_pages_processed_workspace.toLocaleString()}
              </span>
              <span className="p4-kpi-sub">Across all batches</span>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Start a bulk import</h3>
        <ErrorNotice
          error={query.error || submitMutation.error || cancelMutation.error}
        />
        <div className="form-grid">
          <Field label="Batch name">
            <input
              value={batchReference}
              onChange={(e) => setBatchReference(e.target.value)}
              placeholder="e.g. 2025-historical-receipts"
            />
          </Field>
          <Field label="Where are the documents coming from?">
            <select
              value={sourceChannel}
              onChange={(e) =>
                setSourceChannel(e.target.value as typeof sourceChannel)
              }
            >
              <option value="archive_backfill_zip">ZIP archive upload</option>
              <option value="erp_historical_pdfs">
                Previous accounting software PDFs
              </option>
              <option value="supplier_portal_bulk">
                Connected supplier portals
              </option>
              <option value="multi_entity_box_import">
                Shared cloud folder
              </option>
            </select>
          </Field>
          <Field label="Estimated number of pages">
            <input
              type="number"
              min={1}
              max={5000000}
              value={totalPages}
              onChange={(e) => setTotalPages(e.target.value)}
            />
          </Field>
          <Field label="Processing speed">
            <select
              value={workerLane}
              onChange={(e) =>
                setWorkerLane(e.target.value as typeof workerLane)
              }
            >
              <option value="bulk_background">
                Background queue (recommended for large archives)
              </option>
              <option value="live_priority">Priority queue</option>
            </select>
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            busy={submitMutation.isPending}
            onClick={() => submitMutation.mutate()}
          >
            Start bulk import
          </Button>
        </div>
        {bulkNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "12px" }}
          >
            {bulkNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Import batches</h3>
          <Table
            caption="Bulk document import batches"
            emptyText="No bulk imports started yet."
            rows={query.data.batches}
            rowKey={(batch) => batch.id}
            columns={[
              {
                label: "Batch",
                render: (batch) => (
                  <div>
                    <strong>{batch.batch_reference}</strong>
                    <div className="muted" style={{ fontSize: "0.78rem" }}>
                      {humanize(batch.source_channel)}
                    </div>
                  </div>
                ),
              },
              {
                label: "Queue",
                render: (batch) => <Badge>{humanize(batch.worker_lane)}</Badge>,
              },
              {
                label: "Progress",
                render: (batch) =>
                  `${batch.processed_pages.toLocaleString()} / ${batch.total_pages.toLocaleString()} pages`,
              },
              {
                label: "Status",
                render: (batch) => <Badge>{humanize(batch.status)}</Badge>,
              },
              {
                label: "Action",
                render: (batch) =>
                  batch.status !== "cancelled" ? (
                    <Button
                      variant="danger"
                      busy={cancelMutation.isPending}
                      onClick={() => cancelMutation.mutate(batch.id)}
                    >
                      Cancel
                    </Button>
                  ) : (
                    <span className="muted">Cancelled</span>
                  ),
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function P4RegionalDisasterRecoveryHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [primaryCell, setPrimaryCell] = useState<
    | "us-east-va-cell-1"
    | "eu-central-fra-cell-1"
    | "me-central-dxb-cell-1"
    | "ap-south-khi-cell-1"
  >("eu-central-fra-cell-1");
  const [standbyCell, setStandbyCell] = useState<
    | "us-east-va-cell-1"
    | "eu-central-fra-cell-1"
    | "me-central-dxb-cell-1"
    | "ap-south-khi-cell-1"
  >("us-east-va-cell-1");
  const [drillMode, setDrillMode] = useState<
    "fenced_failover" | "failback_to_primary" | "replication_consistency_check"
  >("fenced_failover");
  const [drNotice, setDrNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p4-disaster-recovery"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p4/disaster-recovery", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        topology: {
          pinned_primary_cell: string;
          active_serving_cell: string;
          latest_fencing_token: number;
          split_brain_fencing: string;
          replication_mode: string;
          target_rto_seconds: number;
          target_rpo_seconds: number;
        };
        drills: Array<{
          id: string;
          primary_cell: string;
          standby_cell: string;
          active_cell: string;
          drill_mode: string;
          fencing_token: number;
          split_brain_prevented: boolean;
          achieved_rto_seconds: number;
          achieved_rpo_seconds: number;
          ledger_journal_count: number;
          evidence_file_count: number;
          consistency_digest_sha256: string;
          status: string;
          created_at: string;
        }>;
      },
  });

  const drillMutation = useMutation({
    mutationFn: async (overrideMode?: typeof drillMode) =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p4/disaster-recovery/drill",
          {
            params: { path },
            body: {
              primary_cell: primaryCell,
              standby_cell: standbyCell,
              drill_mode: overrideMode || drillMode,
            },
          },
        ),
      )) as unknown as {
        active_cell: string;
        fencing_token: number;
        achieved_rto_seconds: number;
        achieved_rpo_seconds: number;
        consistency_digest_sha256: string;
        status: string;
      },
    onSuccess: (res) => {
      setDrNotice(
        `Recovery test complete (${humanize(res.status)}). Active region: ${regionName(res.active_cell)} (recovered in ${res.achieved_rto_seconds}s with zero lost records).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-disaster-recovery"],
      });
    },
  });

  const topo = query.data?.topology;

  return (
    <div className="stack">
      <div className="panel">
        <h2>Backup &amp; disaster recovery</h2>
        <p className="muted">
          Verify that your workspace backups stay synchronized in a second
          geographic region and test switching regions safely with zero lost
          accounting entries.
        </p>
        {topo && (
          <div className="p4-kpi-grid">
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Active region</span>
              <span className="p4-kpi-value">
                {regionName(topo.active_serving_cell)}
              </span>
              <span className="p4-kpi-sub">
                Primary region: {regionName(topo.pinned_primary_cell)}
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Data protection</span>
              <span className="p4-kpi-value">Zero data loss</span>
              <span className="p4-kpi-sub">
                Duplicate-write protection active
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Target recovery speed</span>
              <span className="p4-kpi-value">
                Under {topo.target_rto_seconds} seconds
              </span>
              <span className="p4-kpi-sub">Continuous backup sync</span>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Run a backup readiness test</h3>
        <ErrorNotice error={query.error || drillMutation.error} />
        <div className="form-grid">
          <Field label="Primary region">
            <select
              value={primaryCell}
              onChange={(e) =>
                setPrimaryCell(e.target.value as typeof primaryCell)
              }
            >
              <option value="eu-central-fra-cell-1">Europe (Frankfurt)</option>
              <option value="us-east-va-cell-1">
                United States (Virginia)
              </option>
              <option value="me-central-dxb-cell-1">Middle East (Dubai)</option>
              <option value="ap-south-khi-cell-1">South Asia (Karachi)</option>
            </select>
          </Field>
          <Field label="Backup region (must be different)">
            <select
              value={standbyCell}
              onChange={(e) =>
                setStandbyCell(e.target.value as typeof standbyCell)
              }
            >
              <option value="us-east-va-cell-1">
                United States (Virginia)
              </option>
              <option value="eu-central-fra-cell-1">Europe (Frankfurt)</option>
              <option value="me-central-dxb-cell-1">Middle East (Dubai)</option>
              <option value="ap-south-khi-cell-1">South Asia (Karachi)</option>
            </select>
          </Field>
          <Field label="Type of test">
            <select
              value={drillMode}
              onChange={(e) => setDrillMode(e.target.value as typeof drillMode)}
            >
              <option value="fenced_failover">Switch to backup region</option>
              <option value="failback_to_primary">
                Return to primary region
              </option>
              <option value="replication_consistency_check">
                Verify backup completeness only
              </option>
            </select>
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            busy={drillMutation.isPending}
            onClick={() => drillMutation.mutate(undefined)}
          >
            Run recovery test
          </Button>
          <Button
            variant="secondary"
            busy={drillMutation.isPending}
            onClick={() => drillMutation.mutate("failback_to_primary")}
          >
            Switch back to primary
          </Button>
        </div>
        {drNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "12px" }}
          >
            {drNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Past recovery tests</h3>
          <Table
            caption="Disaster recovery test history"
            emptyText="No recovery tests run yet."
            rows={query.data.drills}
            rowKey={(d) => d.id}
            columns={[
              {
                label: "When",
                render: (d) => formatWhen(d.created_at),
              },
              {
                label: "Test",
                render: (d) => <Badge>{humanize(d.drill_mode)}</Badge>,
              },
              {
                label: "Active region",
                render: (d) =>
                  `${regionName(d.active_cell)} (primary: ${regionName(d.primary_cell)})`,
              },
              {
                label: "Recovery time",
                render: (d) => `${d.achieved_rto_seconds}s`,
              },
              {
                label: "Verified records",
                render: (d) =>
                  `${d.ledger_journal_count} entries · ${d.evidence_file_count} documents`,
              },
              {
                label: "Status",
                render: (d) => <Badge tone="good">{humanize(d.status)}</Badge>,
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}

export function P4InventoryCostingHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [sku, setSku] = useState("");
  const [itemName, setItemName] = useState("");
  const [warehouse, setWarehouse] = useState("");
  const [costingMethod, setCostingMethod] = useState<
    "fifo" | "weighted_average"
  >("fifo");
  const [initQty, setInitQty] = useState("100");
  const [initUnitCost, setInitUnitCost] = useState("12.50");
  const [initLandedCost, setInitLandedCost] = useState("0.00");
  const [reorderPoint, setReorderPoint] = useState("20");

  const [selectedItemId, setSelectedItemId] = useState("");
  const [movementKind, setMovementKind] = useState<
    "receipt" | "landed_cost_allocation" | "issue_sale" | "customer_return"
  >("receipt");
  const [movRef, setMovRef] = useState("");
  const [movQty, setMovQty] = useState("25");
  const [movUnitCost, setMovUnitCost] = useState("14.00");
  const [movLandedCost, setMovLandedCost] = useState("0.00");
  const [invNotice, setInvNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p4-inventory"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p4/inventory", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        valuation_summary: {
          total_skus: number;
          total_units_on_hand: string;
          total_subledger_valuation: string;
          total_cogs_recognized: string;
          subledger_to_gl_tied_out: boolean;
        };
        items: Array<{
          id: string;
          sku: string;
          name: string;
          warehouse_location: string;
          costing_method: string;
          currency: string;
          quantity_on_hand: string;
          unit_cost: string;
          total_valuation: string;
          fifo_layers: Array<{ qty: string; unit_cost: string }>;
          reorder_point: string;
          low_stock: boolean;
        }>;
        movements: Array<{
          id: string;
          item_id: string;
          sku: string;
          item_name: string;
          movement_kind: string;
          reference: string;
          accounting_date: string;
          quantity_delta: string;
          unit_cost: string;
          landed_cost_amount: string;
          total_cost_impact: string;
          cogs_amount: string;
          resulting_qty: string;
          resulting_valuation: string;
          source_draft_id: string | null;
        }>;
      },
  });

  const activeItemId = selectedItemId || query.data?.items[0]?.id || "";

  const createItemMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p4/inventory/items", {
          params: { path },
          body: {
            sku: sku || "ITEM-001",
            name: itemName || "Inventory Item",
            warehouse_location: warehouse || "Main Warehouse",
            costing_method: costingMethod,
            currency: b.currency || "USD",
            initial_quantity: initQty || "0",
            initial_unit_cost: initUnitCost || "0",
            initial_landed_cost: initLandedCost || "0",
            reorder_point: reorderPoint || "5",
          },
        }),
      )) as unknown as {
        id: string;
        sku: string;
        quantity_on_hand: string;
        total_valuation: string;
      },
    onSuccess: (res) => {
      setSelectedItemId(res.id);
      setInvNotice(
        `Saved item ${res.sku}: ${res.quantity_on_hand} in stock (${b.currency} ${res.total_valuation} total value).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-inventory"],
      });
    },
  });

  const movementMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/p4/inventory/movements", {
          params: { path },
          body: {
            item_id: activeItemId,
            movement_kind: movementKind,
            reference: movRef || "STOCK-MOVE",
            quantity: movQty || "0",
            unit_cost: movUnitCost || "0",
            landed_cost_amount: movLandedCost || "0",
          },
        }),
      )) as unknown as {
        sku: string;
        movement_kind: string;
        cogs_amount: string;
        resulting_qty: string;
        resulting_valuation: string;
      },
    onSuccess: (res) => {
      setInvNotice(
        `Recorded ${humanize(res.movement_kind).toLowerCase()} for ${res.sku}: ${res.resulting_qty} on hand (${b.currency} ${res.resulting_valuation}).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-inventory"],
      });
    },
  });

  const sum = query.data?.valuation_summary;

  return (
    <div className="stack">
      <div className="panel">
        <h2>Inventory &amp; stock valuation</h2>
        <p className="muted">
          Track products in stock across warehouses, add shipping or customs
          costs, prevent negative stock mistakes, and automatically prepare
          cost-of-goods-sold drafts when items are sold.
        </p>
        {sum && (
          <div className="p4-kpi-grid">
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Products tracked</span>
              <span className="p4-kpi-value">{sum.total_skus}</span>
              <span className="p4-kpi-sub">
                {sum.total_units_on_hand} total units in stock
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Inventory value</span>
              <span className="p4-kpi-value">
                {b.currency} {sum.total_subledger_valuation}
              </span>
              <span className="p4-kpi-sub">
                <Badge tone={sum.subledger_to_gl_tied_out ? "good" : "warning"}>
                  {sum.subledger_to_gl_tied_out
                    ? "Matches general ledger"
                    : "Review needed"}
                </Badge>
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Cost of goods sold</span>
              <span className="p4-kpi-value">
                {b.currency} {sum.total_cogs_recognized}
              </span>
              <span className="p4-kpi-sub">Recorded from sales</span>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Add a product</h3>
        <ErrorNotice error={query.error || createItemMutation.error} />
        <div className="form-grid">
          <Field label="Product code (SKU)">
            <input
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              placeholder="e.g. LAPTOP-14-PRO"
            />
          </Field>
          <Field label="Product name">
            <input
              value={itemName}
              onChange={(e) => setItemName(e.target.value)}
              placeholder="e.g. 14-inch Business Laptop"
            />
          </Field>
          <Field label="Warehouse or location">
            <input
              value={warehouse}
              onChange={(e) => setWarehouse(e.target.value)}
              placeholder="e.g. Main Warehouse"
            />
          </Field>
          <Field label="Valuation method">
            <select
              value={costingMethod}
              onChange={(e) =>
                setCostingMethod(e.target.value as typeof costingMethod)
              }
            >
              <option value="fifo">First in, first out (FIFO)</option>
              <option value="weighted_average">Weighted average cost</option>
            </select>
          </Field>
          <Field label="Opening quantity">
            <input
              type="number"
              step="0.01"
              value={initQty}
              onChange={(e) => setInitQty(e.target.value)}
            />
          </Field>
          <Field label="Opening cost per unit">
            <input
              type="number"
              step="0.01"
              value={initUnitCost}
              onChange={(e) => setInitUnitCost(e.target.value)}
            />
          </Field>
          <Field label="Shipping or customs cost (optional)">
            <input
              type="number"
              step="0.01"
              value={initLandedCost}
              onChange={(e) => setInitLandedCost(e.target.value)}
            />
          </Field>
          <Field label="Low-stock alert level">
            <input
              type="number"
              step="0.01"
              value={reorderPoint}
              onChange={(e) => setReorderPoint(e.target.value)}
            />
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            busy={createItemMutation.isPending}
            onClick={() => createItemMutation.mutate()}
          >
            Save product
          </Button>
        </div>
      </div>

      <div className="panel">
        <h3>Record stock received or sold</h3>
        <ErrorNotice error={movementMutation.error} />
        <div className="form-grid">
          <Field label="Product">
            <select
              value={activeItemId}
              onChange={(e) => setSelectedItemId(e.target.value)}
            >
              {!query.data?.items.length && (
                <option value="">Add a product above first</option>
              )}
              {(query.data?.items || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.sku} — {item.name} ({item.quantity_on_hand} in stock)
                </option>
              ))}
            </select>
          </Field>
          <Field label="Movement type">
            <select
              value={movementKind}
              onChange={(e) =>
                setMovementKind(e.target.value as typeof movementKind)
              }
            >
              <option value="receipt">Stock received (purchase)</option>
              <option value="landed_cost_allocation">
                Add freight or customs cost
              </option>
              <option value="issue_sale">Stock sold (records cost)</option>
              <option value="customer_return">Customer return</option>
            </select>
          </Field>
          <Field label="Order or invoice reference">
            <input
              value={movRef}
              onChange={(e) => setMovRef(e.target.value)}
              placeholder="e.g. PO-2026-041"
            />
          </Field>
          <Field label="Quantity">
            <input
              type="number"
              step="0.01"
              value={movQty}
              onChange={(e) => setMovQty(e.target.value)}
            />
          </Field>
          <Field label="Unit cost (for purchases / returns)">
            <input
              type="number"
              step="0.01"
              value={movUnitCost}
              onChange={(e) => setMovUnitCost(e.target.value)}
            />
          </Field>
          <Field label="Freight or duty amount (optional)">
            <input
              type="number"
              step="0.01"
              value={movLandedCost}
              onChange={(e) => setMovLandedCost(e.target.value)}
            />
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            variant="secondary"
            disabled={!activeItemId}
            busy={movementMutation.isPending}
            onClick={() => movementMutation.mutate()}
          >
            Record stock movement
          </Button>
        </div>
        {invNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "12px" }}
          >
            {invNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Current stock &amp; valuation</h3>
          <Table
            caption="Current inventory items"
            emptyText="No products added yet."
            rows={query.data.items}
            rowKey={(i) => i.id}
            columns={[
              {
                label: "Product",
                render: (i) => (
                  <div>
                    <strong>{i.sku}</strong> · {i.name}
                    <div className="muted" style={{ fontSize: "0.78rem" }}>
                      {i.warehouse_location}
                    </div>
                  </div>
                ),
              },
              {
                label: "Method",
                render: (i) => <Badge>{humanize(i.costing_method)}</Badge>,
              },
              {
                label: "In stock",
                render: (i) => (
                  <div className="p4-inline-actions">
                    <strong>{i.quantity_on_hand}</strong>
                    {i.low_stock && <Badge tone="warning">Low stock</Badge>}
                  </div>
                ),
              },
              {
                label: "Unit cost",
                render: (i) => `${i.currency} ${i.unit_cost}`,
              },
              {
                label: "Total value",
                render: (i) => (
                  <strong>
                    {i.currency} {i.total_valuation}
                  </strong>
                ),
              },
            ]}
          />
          {query.data.movements.length > 0 && (
            <div style={{ marginTop: "20px" }}>
              <h4>Recent stock movements</h4>
              <Table
                caption="Recent stock movements"
                rows={query.data.movements}
                rowKey={(m) => m.id}
                columns={[
                  { label: "Date", render: (m) => m.accounting_date },
                  { label: "Product", render: (m) => <strong>{m.sku}</strong> },
                  {
                    label: "Type",
                    render: (m) => <Badge>{humanize(m.movement_kind)}</Badge>,
                  },
                  { label: "Reference", render: (m) => m.reference },
                  { label: "Quantity", render: (m) => m.quantity_delta },
                  {
                    label: "Updated stock",
                    render: (m) =>
                      `${m.resulting_qty} units (${b.currency} ${m.resulting_valuation})`,
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

export function P4GroupConsolidationHub() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [entityCode, setEntityCode] = useState("");
  const [entityName, setEntityName] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [funcCurrency, setFuncCurrency] = useState("EUR");
  const [ownershipPct, setOwnershipPct] = useState("100.00");
  const [closingFx, setClosingFx] = useState("1.0800");
  const [averageFx, setAverageFx] = useState("1.0500");
  const [saAssets, setSaAssets] = useState("250000.00");
  const [saLiabilities, setSaLiabilities] = useState("90000.00");
  const [saRevenue, setSaRevenue] = useState("180000.00");
  const [saExpenses, setSaExpenses] = useState("120000.00");
  const [icReceivable, setIcReceivable] = useState("0.00");
  const [icPayable, setIcPayable] = useState("0.00");
  const [icRevenue, setIcRevenue] = useState("0.00");
  const [icExpense, setIcExpense] = useState("0.00");

  const [periodLabel, setPeriodLabel] = useState("");
  const [reportingCurrency, setReportingCurrency] = useState(
    b.currency || "USD",
  );
  const [consNotice, setConsNotice] = useState("");

  const query = useQuery({
    queryKey: [...keys.business(b.id), "p4-group-consolidation"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/p4/group-consolidation", {
          signal,
          params: { path },
        }),
      )) as unknown as {
        entities: Array<{
          id: string;
          entity_code: string;
          entity_name: string;
          jurisdiction: string;
          functional_currency: string;
          ownership_percent: number;
          consolidation_method: string;
          effective_from: string;
          closing_fx_rate: string;
          average_fx_rate: string;
          standalone_assets: string;
          standalone_liabilities: string;
          standalone_revenue: string;
          standalone_expenses: string;
          intercompany_receivable: string;
          intercompany_payable: string;
          intercompany_revenue: string;
          intercompany_expense: string;
        }>;
        runs: Array<{
          id: string;
          period_label: string;
          reporting_currency: string;
          entities_count: number;
          intercompany_ar_eliminated: string;
          intercompany_rev_eliminated: string;
          intercompany_mismatch_amount: string;
          fx_cta_adjustment: string;
          minority_interest_nci_share: string;
          consolidated_assets: string;
          consolidated_liabilities: string;
          consolidated_equity: string;
          consolidated_revenue: string;
          consolidated_expenses: string;
          consolidated_net_income: string;
          parent_attributable_net_income: string;
          elimination_lines: Array<{
            label: string;
            debit: string;
            credit: string;
          }>;
          status: string;
          created_at: string;
        }>;
      },
  });

  const upsertEntityMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p4/group-consolidation/entities",
          {
            params: { path },
            body: {
              entity_code: entityCode || "SUB-01",
              entity_name: entityName || "Subsidiary",
              jurisdiction: jurisdiction || "US",
              functional_currency: funcCurrency,
              ownership_percent: ownershipPct || "100.00",
              closing_fx_rate: closingFx || "1.000000",
              average_fx_rate: averageFx || "1.000000",
              standalone_assets: saAssets || "0.00",
              standalone_liabilities: saLiabilities || "0.00",
              standalone_revenue: saRevenue || "0.00",
              standalone_expenses: saExpenses || "0.00",
              intercompany_receivable: icReceivable || "0.00",
              intercompany_payable: icPayable || "0.00",
              intercompany_revenue: icRevenue || "0.00",
              intercompany_expense: icExpense || "0.00",
            },
          },
        ),
      )) as unknown as {
        entity_code: string;
        entity_name: string;
        ownership_percent: number;
        consolidation_method: string;
      },
    onSuccess: (res) => {
      setConsNotice(
        `Saved company ${res.entity_name} (${res.ownership_percent}% owned).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-group-consolidation"],
      });
    },
  });

  const runConsolidationMutation = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/p4/group-consolidation/run",
          {
            params: { path },
            body: {
              period_label:
                periodLabel || `${new Date().getFullYear()} Group Report`,
              reporting_currency: reportingCurrency || "USD",
            },
          },
        ),
      )) as unknown as {
        period_label: string;
        reporting_currency: string;
        entities_count: number;
        consolidated_net_income: string;
        minority_interest_nci_share: string;
        intercompany_mismatch_amount: string;
        status: string;
      },
    onSuccess: (res) => {
      setConsNotice(
        `Combined group report "${res.period_label}" across ${res.entities_count} companies: net profit ${res.reporting_currency} ${res.consolidated_net_income}.`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "p4-group-consolidation"],
      });
    },
  });

  const latestRun = query.data?.runs[0];

  return (
    <div className="stack">
      <div className="panel">
        <h2>Group reporting</h2>
        <p className="muted">
          Combine financial results across multiple companies or subsidiaries,
          convert foreign currencies into your reporting currency, and remove
          internal sales or loans between group companies.
        </p>
        {latestRun && (
          <div className="p4-kpi-grid">
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Combined net profit</span>
              <span className="p4-kpi-value">
                {latestRun.reporting_currency}{" "}
                {latestRun.consolidated_net_income}
              </span>
              <span className="p4-kpi-sub">
                Parent share: {latestRun.reporting_currency}{" "}
                {latestRun.parent_attributable_net_income}
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Outside owners&apos; share</span>
              <span className="p4-kpi-value">
                {latestRun.reporting_currency}{" "}
                {latestRun.minority_interest_nci_share}
              </span>
              <span className="p4-kpi-sub">
                Currency translation: {latestRun.fx_cta_adjustment}
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Combined equity</span>
              <span className="p4-kpi-value">
                {latestRun.reporting_currency} {latestRun.consolidated_equity}
              </span>
              <span className="p4-kpi-sub">
                Assets {latestRun.consolidated_assets} − Liabilities{" "}
                {latestRun.consolidated_liabilities}
              </span>
            </div>
            <div className="p4-kpi-card">
              <span className="p4-kpi-label">Internal balances removed</span>
              <span className="p4-kpi-value">
                {latestRun.intercompany_ar_eliminated}
              </span>
              <span className="p4-kpi-sub">
                <Badge
                  tone={
                    latestRun.status === "consolidated_balanced"
                      ? "good"
                      : "warning"
                  }
                >
                  {humanize(latestRun.status)}
                </Badge>
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="panel">
        <h3>Add or update a group company</h3>
        <ErrorNotice error={query.error || upsertEntityMutation.error} />
        <div className="form-grid">
          <Field label="Short company code">
            <input
              value={entityCode}
              onChange={(e) => setEntityCode(e.target.value)}
              placeholder="e.g. UK-SUB"
            />
          </Field>
          <Field label="Legal company name">
            <input
              value={entityName}
              onChange={(e) => setEntityName(e.target.value)}
              placeholder="e.g. Acme Europe Ltd"
            />
          </Field>
          <Field label="Country or state">
            <input
              value={jurisdiction}
              onChange={(e) => setJurisdiction(e.target.value)}
              placeholder="e.g. United Kingdom"
            />
          </Field>
          <Field label="Local currency">
            <input
              maxLength={3}
              value={funcCurrency}
              onChange={(e) => setFuncCurrency(e.target.value.toUpperCase())}
              placeholder="e.g. EUR"
            />
          </Field>
          <Field label="Ownership percentage (%)">
            <input
              type="number"
              step="0.01"
              min={0.01}
              max={100}
              value={ownershipPct}
              onChange={(e) => setOwnershipPct(e.target.value)}
            />
          </Field>
          <Field label="Period-end exchange rate">
            <input
              type="number"
              step="0.0001"
              value={closingFx}
              onChange={(e) => setClosingFx(e.target.value)}
            />
          </Field>
          <Field label="Average exchange rate">
            <input
              type="number"
              step="0.0001"
              value={averageFx}
              onChange={(e) => setAverageFx(e.target.value)}
            />
          </Field>
          <Field label="Total assets">
            <input
              type="number"
              step="0.01"
              value={saAssets}
              onChange={(e) => setSaAssets(e.target.value)}
            />
          </Field>
          <Field label="Total liabilities">
            <input
              type="number"
              step="0.01"
              value={saLiabilities}
              onChange={(e) => setSaLiabilities(e.target.value)}
            />
          </Field>
          <Field label="Revenue">
            <input
              type="number"
              step="0.01"
              value={saRevenue}
              onChange={(e) => setSaRevenue(e.target.value)}
            />
          </Field>
          <Field label="Expenses">
            <input
              type="number"
              step="0.01"
              value={saExpenses}
              onChange={(e) => setSaExpenses(e.target.value)}
            />
          </Field>
        </div>
        <details className="p3-advanced-details">
          <summary>
            Balances with other companies in your group (optional)
          </summary>
          <div className="form-grid">
            <Field label="Owed by other group companies">
              <input
                type="number"
                step="0.01"
                value={icReceivable}
                onChange={(e) => setIcReceivable(e.target.value)}
              />
            </Field>
            <Field label="Owed to other group companies">
              <input
                type="number"
                step="0.01"
                value={icPayable}
                onChange={(e) => setIcPayable(e.target.value)}
              />
            </Field>
            <Field label="Sales to other group companies">
              <input
                type="number"
                step="0.01"
                value={icRevenue}
                onChange={(e) => setIcRevenue(e.target.value)}
              />
            </Field>
            <Field label="Purchases from other group companies">
              <input
                type="number"
                step="0.01"
                value={icExpense}
                onChange={(e) => setIcExpense(e.target.value)}
              />
            </Field>
          </div>
        </details>
        <div className="p3-actions-row">
          <Button
            busy={upsertEntityMutation.isPending}
            onClick={() => upsertEntityMutation.mutate()}
          >
            Save company
          </Button>
        </div>
      </div>

      <div className="panel">
        <h3>Create combined group report</h3>
        <ErrorNotice error={runConsolidationMutation.error} />
        <div className="form-grid">
          <Field label="Reporting period">
            <input
              value={periodLabel}
              onChange={(e) => setPeriodLabel(e.target.value)}
              placeholder="e.g. Q4 2026"
            />
          </Field>
          <Field label="Group reporting currency">
            <input
              maxLength={3}
              value={reportingCurrency}
              onChange={(e) =>
                setReportingCurrency(e.target.value.toUpperCase())
              }
            />
          </Field>
        </div>
        <div className="p3-actions-row">
          <Button
            variant="secondary"
            busy={runConsolidationMutation.isPending}
            onClick={() => runConsolidationMutation.mutate()}
          >
            Generate group report
          </Button>
        </div>
        {consNotice && (
          <p
            role="status"
            className="success-text"
            style={{ marginTop: "12px" }}
          >
            {consNotice}
          </p>
        )}
      </div>

      {query.data && (
        <div className="panel">
          <h3>Companies &amp; past group reports</h3>
          <Table
            caption="Group companies"
            emptyText="No group companies added yet."
            rows={query.data.entities}
            rowKey={(e) => e.id}
            columns={[
              {
                label: "Company",
                render: (e) => (
                  <div>
                    <strong>{e.entity_code}</strong> · {e.entity_name}
                    <div className="muted" style={{ fontSize: "0.78rem" }}>
                      {e.jurisdiction} · {e.functional_currency}
                    </div>
                  </div>
                ),
              },
              {
                label: "Ownership",
                render: (e) => (
                  <Badge>
                    {e.ownership_percent}% · {humanize(e.consolidation_method)}
                  </Badge>
                ),
              },
              {
                label: "Assets / liabilities",
                render: (e) =>
                  `${e.standalone_assets} / ${e.standalone_liabilities}`,
              },
              {
                label: "Revenue / expenses",
                render: (e) =>
                  `${e.standalone_revenue} / ${e.standalone_expenses}`,
              },
            ]}
          />
          {query.data.runs.length > 0 && (
            <div style={{ marginTop: "20px" }}>
              <h4>Saved group reports</h4>
              <Table
                caption="Saved group reports"
                rows={query.data.runs}
                rowKey={(r) => r.id}
                columns={[
                  {
                    label: "Period",
                    render: (r) => (
                      <strong>
                        {r.period_label} ({r.entities_count} companies)
                      </strong>
                    ),
                  },
                  {
                    label: "Combined assets / equity",
                    render: (r) =>
                      `${r.reporting_currency} ${r.consolidated_assets} / ${r.consolidated_equity}`,
                  },
                  {
                    label: "Combined net profit",
                    render: (r) =>
                      `${r.reporting_currency} ${r.consolidated_net_income}`,
                  },
                  {
                    label: "Status",
                    render: (r) => (
                      <Badge
                        tone={
                          r.status === "consolidated_balanced"
                            ? "good"
                            : "warning"
                        }
                      >
                        {humanize(r.status)}
                      </Badge>
                    ),
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

const TAB_GROUPS: Record<
  AutomationPageMeta["group"],
  ReadonlyArray<readonly [AutomationTabId, string]>
> = {
  workflows: (["reminders", "portals", "payments", "sla"] as const).map(
    (id) => [id, AUTOMATION_PAGE_CONFIG[id].shortLabel] as const,
  ),
  documents: (
    ["payouts", "voice", "ai-benchmarks", "accounting-expansion"] as const
  ).map((id) => [id, AUTOMATION_PAGE_CONFIG[id].shortLabel] as const),
  operations: (
    [
      "p4-inventory",
      "p4-group-consolidation",
      "p4-bulk-ingestion",
      "p4-migrations",
    ] as const
  ).map((id) => [id, AUTOMATION_PAGE_CONFIG[id].shortLabel] as const),
  security: (
    [
      "p4-operations",
      "p4-disaster-recovery",
      "encryption",
      "enterprise-sso",
    ] as const
  ).map((id) => [id, AUTOMATION_PAGE_CONFIG[id].shortLabel] as const),
};

const WORKSPACE_GOVERNANCE_SUBTABS: ReadonlyArray<
  readonly [AutomationTabId, string]
> = (["p4-operations", "encryption", "enterprise-sso"] as const).map(
  (id) => [id, AUTOMATION_PAGE_CONFIG[id].shortLabel] as const,
);

export function P3AutomationHub({
  initialTab = "reminders",
  standalone = false,
  onRouteChange,
}: {
  initialTab?: AutomationTabId;
  standalone?: boolean;
  onRouteChange?: (path: string) => void;
}) {
  const [tab, setTab] = useState<AutomationTabId>(initialTab);
  useEffect(() => {
    setTab(initialTab);
  }, [initialTab]);
  const handleTabChange = (nextTab: string) => {
    const validTab = nextTab as AutomationTabId;
    setTab(validTab);
    const targetPath = AUTOMATION_PAGE_CONFIG[validTab]?.route;
    if (targetPath) {
      if (onRouteChange) {
        onRouteChange(targetPath);
      } else if (typeof window !== "undefined") {
        window.history.replaceState(null, "", targetPath);
      }
    }
  };

  const activeMeta =
    AUTOMATION_PAGE_CONFIG[tab] || AUTOMATION_PAGE_CONFIG.reminders;
  const groupItems = TAB_GROUPS[activeMeta.group] || TAB_GROUPS.workflows;
  const standaloneItems =
    standalone && activeMeta.standaloneSubgroup === "workspace-governance"
      ? WORKSPACE_GOVERNANCE_SUBTABS
      : null;

  return (
    <>
      <PageHeading
        eyebrow={activeMeta.eyebrow}
        title={activeMeta.title}
        description={activeMeta.description}
      />
      {!standalone && (
        <>
          <SectionTabs
            id="automation-categories"
            label="Tool categories"
            value={activeMeta.group}
            onChange={(nextGroup) => {
              const first =
                TAB_GROUPS[nextGroup as AutomationPageMeta["group"]]?.[0]?.[0];
              if (first) handleTabChange(first);
            }}
            items={[
              ["workflows", "Payments & reminders"],
              ["documents", "Bank feeds & documents"],
              ["operations", "Inventory & group"],
              ["security", "Security & workspace"],
            ]}
          />
          <SectionTabs
            id="automation-sections"
            label="Section tools"
            value={tab}
            onChange={handleTabChange}
            items={groupItems}
          />
        </>
      )}
      {standaloneItems && (
        <SectionTabs
          id="workspace-governance-sections"
          label="Workspace governance"
          value={tab}
          onChange={handleTabChange}
          items={standaloneItems}
        />
      )}
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
      {tab === "p4-operations" && <P4EnterpriseOperationsHub />}
      {tab === "p4-migrations" && <P4OnlineLedgerMigrationsHub />}
      {tab === "p4-bulk-ingestion" && <P4BulkIngestionHub />}
      {tab === "p4-disaster-recovery" && <P4RegionalDisasterRecoveryHub />}
      {tab === "p4-inventory" && <P4InventoryCostingHub />}
      {tab === "p4-group-consolidation" && <P4GroupConsolidationHub />}
    </>
  );
}
