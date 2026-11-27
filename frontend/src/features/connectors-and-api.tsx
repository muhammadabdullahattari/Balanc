import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  PageHeading,
  Table,
  humanize,
} from "../components/ui";
import { useBusiness } from "./context";

type ConnectorTab = "connectors" | "developer";

interface ConnectedAccountItem {
  id: string;
  connector_kind: string;
  display_name: string;
  environment: string;
  status: string;
}

interface ConnectorsOverviewResponse {
  inbound_invoice_email: string;
  accounts: ConnectedAccountItem[];
}

interface ApiKeyItem {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  revoked: boolean;
}

interface WebhookEndpointItem {
  id: string;
  target_url: string;
  events: string[];
}

interface DeveloperPlatformResponse {
  available_scopes: string[];
  api_keys: ApiKeyItem[];
  webhooks: WebhookEndpointItem[];
}

export function ConnectorsAndApi({
  initialTab = "connectors",
}: {
  initialTab?: ConnectorTab;
}) {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [tab, setTab] = useState<ConnectorTab>(initialTab);
  const [connectorKind, setConnectorKind] = useState<
    | "plaid"
    | "stripe_fc"
    | "quickbooks"
    | "xero"
    | "shopify"
    | "square"
    | "google_drive"
    | "dropbox"
  >("plaid");
  const [displayName, setDisplayName] = useState("");
  const [envMode, setEnvMode] = useState<"sandbox" | "production">("sandbox");
  const [accessToken, setAccessToken] = useState("");
  const [exportTarget, setExportTarget] = useState<"quickbooks" | "xero">(
    "quickbooks",
  );
  const [exportFormat, setExportFormat] = useState<"api_json" | "csv" | "iif">(
    "csv",
  );
  const [exportOutput, setExportOutput] = useState<string>("");

  const [keyName, setKeyName] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<string[]>([
    "read:accounts",
    "read:reports",
  ]);
  const [mintedToken, setMintedToken] = useState<string | null>(null);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookDesc, setWebhookDesc] = useState("");
  const [mintedWebhookSecret, setMintedWebhookSecret] = useState<string | null>(
    null,
  );

  const connectors = useQuery({
    queryKey: [...keys.business(b.id), "connectors-overview"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/connectors", {
          signal,
          params: { path },
        }),
      )) as unknown as ConnectorsOverviewResponse,
  });

  const devPlatform = useQuery({
    queryKey: [...keys.business(b.id), "developer-platform"],
    queryFn: async ({ signal }) =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/developer", {
          signal,
          params: { path },
        }),
      )) as unknown as DeveloperPlatformResponse,
  });

  const connectAccount = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/connectors/accounts", {
          params: { path },
          body: {
            connector_kind: connectorKind,
            display_name:
              displayName.trim() || `${humanize(connectorKind)} feed`,
            environment: envMode,
            access_token: accessToken,
            config: {},
          },
        }),
      ),
    onSuccess: () => {
      setDisplayName("");
      setAccessToken("");
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "connectors-overview"],
      });
    },
  });

  const syncBank = useMutation({
    mutationFn: (connectorId: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/connectors/bank-feeds/{connector_id}/sync",
          {
            params: { path: { ...path, connector_id: connectorId } },
            body: {},
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "connectors-overview"],
      });
    },
  });

  const runExport = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/connectors/accounting/export",
          {
            params: { path },
            body: {
              target_system: exportTarget,
              format: exportFormat,
            },
          },
        ),
      ),
    onSuccess: (res) => {
      const data = res as Record<string, unknown>;
      setExportOutput(String(data.payload || ""));
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "connectors-overview"],
      });
    },
  });

  const runXeroImport = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/connectors/xero/import", {
          params: { path },
          body: { tenant_id: "xero-connected-org", dry_run: false },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "connectors-overview"],
      });
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });

  const syncEcommerce = useMutation({
    mutationFn: (provider: "shopify" | "square") =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/connectors/ecommerce/{provider}/sync",
          {
            params: { path: { ...path, provider } },
            body: {},
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "connectors-overview"],
      });
    },
  });

  const syncDrive = useMutation({
    mutationFn: (provider: "google_drive" | "dropbox") =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/connectors/cloud-drive/{provider}/sync",
          {
            params: { path: { ...path, provider } },
            body: { folder_name: "/Receipts & Invoices" },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "connectors-overview"],
      });
    },
  });

  const createKey = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/developer/api-keys", {
          params: { path },
          body: {
            name: keyName.trim() || "Integration key",
            scopes: selectedScopes,
            rate_limit_per_minute: 120,
            expires_in_days: 90,
          },
        }),
      ),
    onSuccess: (res) => {
      const data = res as Record<string, unknown>;
      setKeyName("");
      setMintedToken(String(data.token || ""));
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "developer-platform"],
      });
    },
  });

  const revokeKey = useMutation({
    mutationFn: (keyId: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/developer/api-keys/{key_id}/revoke",
          {
            params: { path: { ...path, key_id: keyId } },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "developer-platform"],
      });
    },
  });

  const createWebhook = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/developer/webhooks", {
          params: { path },
          body: {
            target_url: webhookUrl.trim(),
            description: webhookDesc.trim(),
            events: ["draft.created", "journal.posted", "invoice.overdue"],
          },
        }),
      ),
    onSuccess: (res) => {
      const data = res as Record<string, unknown>;
      setWebhookUrl("");
      setWebhookDesc("");
      setMintedWebhookSecret(String(data.signing_secret || ""));
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "developer-platform"],
      });
    },
  });

  const testWebhook = useMutation({
    mutationFn: (endpointId: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/developer/webhooks/{endpoint_id}/test",
          {
            params: {
              path: { ...path, endpoint_id: endpointId },
              query: { event_type: "draft.created" },
            },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "developer-platform"],
      });
    },
  });

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  };

  return (
    <>
      <PageHeading
        eyebrow="INTEGRATIONS & PUBLIC API"
        title="Connected feeds & developer access"
        description="Connect bank feeds, import and export QuickBooks or Xero books, sync Shopify and Square sales, forward supplier bills by email, and manage external API tokens."
      />

      <div className="tabs" role="tablist" aria-label="Integration sections">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "connectors"}
          className={tab === "connectors" ? "tab is-active" : "tab"}
          onClick={() => setTab("connectors")}
        >
          Feeds, accounting & cloud folders
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "developer"}
          className={tab === "developer" ? "tab is-active" : "tab"}
          onClick={() => setTab("developer")}
        >
          Developer API keys & webhooks
        </button>
      </div>

      <ErrorNotice
        error={
          connectors.error ||
          devPlatform.error ||
          connectAccount.error ||
          syncBank.error ||
          runExport.error ||
          runXeroImport.error ||
          syncEcommerce.error ||
          syncDrive.error ||
          createKey.error ||
          revokeKey.error ||
          createWebhook.error ||
          testWebhook.error
        }
      />

      {tab === "connectors" ? (
        connectors.isPending ? (
          <Loading />
        ) : (
          <div style={{ display: "grid", gap: "18px" }}>
            <section className="panel">
              <h2>Forward supplier bills by email</h2>
              <p className="muted">
                Ask suppliers to email PDFs or invoices to your dedicated
                workspace inbox. Every attachment is checked for duplicates,
                matched against open purchase orders, and queued for review.
              </p>
              <div
                style={{
                  marginTop: "10px",
                  padding: "12px 14px",
                  borderRadius: "8px",
                  background: "var(--surface-muted, rgba(0,0,0,0.04))",
                  fontFamily: "var(--font-mono, monospace)",
                  fontWeight: 600,
                }}
              >
                {connectors.data?.inbound_invoice_email}
              </div>
            </section>

            <section className="panel">
              <h2>Connect a bank, card, commerce, or folder integration</h2>
              <form
                className="form-grid"
                onSubmit={(e) => {
                  e.preventDefault();
                  connectAccount.mutate();
                }}
              >
                <Field label="Service">
                  <select
                    value={connectorKind}
                    onChange={(e) =>
                      setConnectorKind(e.target.value as typeof connectorKind)
                    }
                  >
                    <option value="plaid">Plaid bank & card feed</option>
                    <option value="stripe_fc">
                      Stripe Financial Connections
                    </option>
                    <option value="quickbooks">QuickBooks Online</option>
                    <option value="xero">Xero Accounting</option>
                    <option value="shopify">Shopify store</option>
                    <option value="square">Square point of sale</option>
                    <option value="google_drive">Google Drive folder</option>
                    <option value="dropbox">Dropbox folder</option>
                  </select>
                </Field>
                <Field label="Account label">
                  <input
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="e.g. Main operating checking"
                  />
                </Field>
                <Field label="Connection mode">
                  <select
                    value={envMode}
                    onChange={(e) =>
                      setEnvMode(e.target.value as "sandbox" | "production")
                    }
                  >
                    <option value="sandbox">Test environment</option>
                    <option value="production">Live production</option>
                  </select>
                </Field>
                <Field label="Connection token (encrypted at rest)">
                  <input
                    type="password"
                    value={accessToken}
                    onChange={(e) => setAccessToken(e.target.value)}
                    placeholder="Paste provider token or leave blank for test feed"
                  />
                </Field>
                <div style={{ display: "flex", alignItems: "end" }}>
                  <Button type="submit" busy={connectAccount.isPending}>
                    Connect integration
                  </Button>
                </div>
              </form>

              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "10px",
                  marginTop: "16px",
                }}
              >
                <Button
                  variant="secondary"
                  busy={runXeroImport.isPending}
                  onClick={() => runXeroImport.mutate()}
                >
                  Import Xero accounts & journals
                </Button>
                <Button
                  variant="secondary"
                  busy={syncEcommerce.isPending}
                  onClick={() => syncEcommerce.mutate("shopify")}
                >
                  Sync Shopify sales & fees
                </Button>
                <Button
                  variant="secondary"
                  busy={syncEcommerce.isPending}
                  onClick={() => syncEcommerce.mutate("square")}
                >
                  Sync Square sales & fees
                </Button>
                <Button
                  variant="secondary"
                  busy={syncDrive.isPending}
                  onClick={() => syncDrive.mutate("google_drive")}
                >
                  Scan Google Drive folder
                </Button>
                <Button
                  variant="secondary"
                  busy={syncDrive.isPending}
                  onClick={() => syncDrive.mutate("dropbox")}
                >
                  Scan Dropbox folder
                </Button>
              </div>
            </section>

            <section className="panel">
              <h2>Export books to QuickBooks or Xero</h2>
              <div className="filterbar">
                <Field label="Target accounting system">
                  <select
                    value={exportTarget}
                    onChange={(e) =>
                      setExportTarget(e.target.value as "quickbooks" | "xero")
                    }
                  >
                    <option value="quickbooks">QuickBooks Online</option>
                    <option value="xero">Xero Accounting</option>
                  </select>
                </Field>
                <Field label="Export format">
                  <select
                    value={exportFormat}
                    onChange={(e) =>
                      setExportFormat(
                        e.target.value as "api_json" | "csv" | "iif",
                      )
                    }
                  >
                    <option value="csv">Spreadsheet import (CSV)</option>
                    <option value="api_json">
                      Structured API payload (JSON)
                    </option>
                    <option value="iif">Desktop journal batch (IIF)</option>
                  </select>
                </Field>
                <div style={{ display: "flex", alignItems: "end" }}>
                  <Button
                    busy={runExport.isPending}
                    onClick={() => runExport.mutate()}
                  >
                    Generate export
                  </Button>
                </div>
              </div>
              {exportOutput && (
                <pre
                  style={{
                    marginTop: "12px",
                    maxHeight: "220px",
                    overflow: "auto",
                    padding: "12px",
                    borderRadius: "8px",
                    background: "var(--surface-muted, rgba(0,0,0,0.04))",
                    fontSize: "12px",
                  }}
                >
                  {exportOutput}
                </pre>
              )}
            </section>

            <section className="panel">
              <h2>Connected accounts & recent sync runs</h2>
              {connectors.data?.accounts.length ? (
                <Table
                  caption="Connected accounts"
                  rows={connectors.data.accounts}
                  rowKey={(r) => r.id}
                  columns={[
                    { label: "Account", render: (r) => r.display_name },
                    {
                      label: "Service",
                      render: (r) => humanize(r.connector_kind),
                    },
                    {
                      label: "Mode",
                      render: (r) =>
                        r.environment === "production" ? "Live" : "Test",
                    },
                    {
                      label: "Status",
                      render: (r) => (
                        <Badge tone="good">{humanize(r.status)}</Badge>
                      ),
                    },
                    {
                      label: "Action",
                      render: (r) =>
                        r.connector_kind === "plaid" ||
                        r.connector_kind === "stripe_fc" ? (
                          <Button
                            variant="ghost"
                            busy={syncBank.isPending}
                            onClick={() => syncBank.mutate(r.id)}
                          >
                            Sync new transactions
                          </Button>
                        ) : (
                          <span className="muted">Ready</span>
                        ),
                    },
                  ]}
                />
              ) : (
                <Empty title="No external accounts connected yet.">
                  Connect a bank feed, store, or cloud folder above to start
                  syncing automatically.
                </Empty>
              )}
            </section>
          </div>
        )
      ) : devPlatform.isPending ? (
        <Loading />
      ) : (
        <div style={{ display: "grid", gap: "18px" }}>
          <section className="panel">
            <h2>Create a scoped API key</h2>
            <p className="muted">
              Personal access tokens grant scoped programmatic access to{" "}
              <code>/public/v1</code> endpoints while keeping workspace row
              isolation enforced.
            </p>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                createKey.mutate();
              }}
            >
              <Field label="Key description">
                <input
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  placeholder="e.g. Custom ERP dashboard"
                  required
                />
              </Field>
              <Field label="Allowed permissions">
                <div style={{ display: "flex", flexWrap: "wrap", gap: "10px" }}>
                  {(
                    devPlatform.data?.available_scopes ?? [
                      "read:accounts",
                      "read:journals",
                      "read:reports",
                      "write:drafts",
                    ]
                  ).map((sc) => (
                    <label
                      key={sc}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "6px",
                        fontSize: "13px",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selectedScopes.includes(sc)}
                        onChange={() => toggleScope(sc)}
                      />
                      {sc}
                    </label>
                  ))}
                </div>
              </Field>
              <div style={{ display: "flex", alignItems: "end" }}>
                <Button
                  type="submit"
                  busy={createKey.isPending}
                  disabled={selectedScopes.length === 0}
                >
                  Create API key
                </Button>
              </div>
            </form>

            {mintedToken && (
              <div
                role="status"
                style={{
                  marginTop: "12px",
                  padding: "12px",
                  borderRadius: "8px",
                  border: "1px solid var(--border-strong, #cbd5e1)",
                }}
              >
                <strong>Copy your new API token now (shown once):</strong>
                <div
                  style={{
                    marginTop: "6px",
                    fontFamily: "var(--font-mono, monospace)",
                    wordBreak: "break-all",
                  }}
                >
                  {mintedToken}
                </div>
              </div>
            )}

            {devPlatform.data?.api_keys.length ? (
              <Table
                caption="Issued API keys"
                rows={devPlatform.data.api_keys}
                rowKey={(r) => r.id}
                columns={[
                  { label: "Name", render: (r) => r.name },
                  { label: "Prefix", render: (r) => `${r.key_prefix}…` },
                  { label: "Scopes", render: (r) => r.scopes.join(", ") },
                  {
                    label: "Status",
                    render: (r) => (
                      <Badge tone={r.revoked ? "warning" : "good"}>
                        {r.revoked ? "Revoked" : "Active"}
                      </Badge>
                    ),
                  },
                  {
                    label: "Action",
                    render: (r) =>
                      r.revoked ? (
                        <span className="muted">Revoked</span>
                      ) : (
                        <Button
                          variant="ghost"
                          busy={revokeKey.isPending}
                          onClick={() => revokeKey.mutate(r.id)}
                        >
                          Revoke
                        </Button>
                      ),
                  },
                ]}
              />
            ) : (
              <Empty title="No API keys created yet.">
                Create a scoped token above to integrate external tools safely.
              </Empty>
            )}
          </section>

          <section className="panel">
            <h2>Outbound signed webhooks</h2>
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                createWebhook.mutate();
              }}
            >
              <Field label="Destination HTTPS URL">
                <input
                  type="url"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://example.com/webhooks/ledgerpilot"
                  required
                />
              </Field>
              <Field label="Description">
                <input
                  value={webhookDesc}
                  onChange={(e) => setWebhookDesc(e.target.value)}
                  placeholder="Notify internal Slack or ERP workflow"
                />
              </Field>
              <div style={{ display: "flex", alignItems: "end" }}>
                <Button type="submit" busy={createWebhook.isPending}>
                  Register webhook
                </Button>
              </div>
            </form>

            {mintedWebhookSecret && (
              <p style={{ marginTop: "10px", fontSize: "13px" }}>
                Signing secret (save now): <code>{mintedWebhookSecret}</code>
              </p>
            )}

            {devPlatform.data?.webhooks.length ? (
              <Table
                caption="Registered webhook endpoints"
                rows={devPlatform.data.webhooks}
                rowKey={(r) => r.id}
                columns={[
                  { label: "Destination", render: (r) => r.target_url },
                  { label: "Events", render: (r) => r.events.join(", ") },
                  {
                    label: "Test delivery",
                    render: (r) => (
                      <Button
                        variant="ghost"
                        busy={testWebhook.isPending}
                        onClick={() => testWebhook.mutate(r.id)}
                      >
                        Send test event
                      </Button>
                    ),
                  },
                ]}
              />
            ) : (
              <Empty title="No webhook endpoints configured.">
                Add an HTTPS destination above to receive signed event
                notifications.
              </Empty>
            )}
          </section>
        </div>
      )}
    </>
  );
}
