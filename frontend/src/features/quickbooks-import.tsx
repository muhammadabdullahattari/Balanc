import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Modal,
  Table,
} from "../components/ui";
import { today, useBusiness } from "./context";

export function QuickBooksImport() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [cutoverDate, setCutoverDate] = useState<string>(today());
  const [selectedEntities, setSelectedEntities] = useState<string[]>([
    "Account",
    "Customer",
    "Vendor",
    "JournalEntry",
    "Invoice",
    "Bill",
    "Payment",
    "CreditMemo",
    "BillPayment",
    "VendorCredit",
  ]);

  const previewQuery = useQuery({
    queryKey: [...keys.business(b.id), "quickbooks", "preview"],
    queryFn: ({ signal }) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/quickbooks/preview", {
          signal,
          params: { path, query: { entity: "Account" } },
        }),
      ),
  });

  const importDryRun = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/quickbooks/import", {
          params: { path, query: { dry_run: true, cutover_date: cutoverDate } },
          body: selectedEntities,
        }),
      ),
  });

  const importLive = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/quickbooks/import", {
          params: {
            path,
            query: { dry_run: false, cutover_date: cutoverDate },
          },
          body: selectedEntities,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id)],
      });
    },
  });

  const toggleEntity = (entity: string) => {
    importDryRun.reset();
    importLive.reset();
    setSelectedEntities((prev) =>
      prev.includes(entity)
        ? prev.filter((e) => e !== entity)
        : [...prev, entity],
    );
  };

  const dryRunData = importDryRun.data as
    | {
        status?: string;
        imported_accounts?: number;
        imported_parties?: number;
        imported_journals?: number;
        imported_invoices?: number;
        imported_bills?: number;
        imported_payments?: number;
        imported_credits?: number;
        imported_bill_payments?: number;
        imported_vendor_credits?: number;
        skipped_duplicates?: number;
        unmapped_accounts?: Array<{
          code?: string;
          name?: string;
          journal_id?: string;
        }>;
        total_debit?: string;
        total_credit?: string;
        reconciled?: boolean;
      }
    | undefined;

  const liveData = importLive.data as
    | {
        status?: string;
        imported_accounts?: number;
        imported_parties?: number;
        imported_journals?: number;
        imported_invoices?: number;
        imported_bills?: number;
        imported_payments?: number;
        imported_credits?: number;
        imported_bill_payments?: number;
        imported_vendor_credits?: number;
        skipped_duplicates?: number;
        reconciled?: boolean;
      }
    | undefined;

  const unmapped = dryRunData?.unmapped_accounts || [];
  const previewMeta = previewQuery.data as
    | {
        connection_mode?: "custom_company" | "oauth_connected" | "sandbox_demo";
        realm_id?: string;
        company_name?: string;
        oauth_configured?: boolean;
      }
    | undefined;

  const [qbStatusNotice, setQbStatusNotice] = useState<string>("");
  const [confirmLiveImport, setConfirmLiveImport] = useState(false);

  const oauthAuthorize = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/quickbooks/authorize", {
          params: { path },
        }),
      ),
    onSuccess: (res) => {
      const targetUrl = (res as { url?: string })?.url;
      if (targetUrl) {
        window.location.assign(targetUrl);
      }
    },
  });

  const connectQuickBooks = useMutation({
    mutationFn: (payload: {
      mode: "custom_credentials" | "sandbox_demo";
      company_name?: string;
      realm_id?: string;
      access_token?: string;
      refresh_token?: string;
      environment?: "sandbox" | "production";
    }) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/quickbooks/connect", {
          params: { path },
          body: {
            mode: payload.mode,
            company_name: payload.company_name || "",
            realm_id: payload.realm_id || "",
            access_token: payload.access_token || "",
            refresh_token: payload.refresh_token || "",
            environment: payload.environment || "sandbox",
          },
        }),
      ),
    onSuccess: (res) => {
      const data = res as {
        company_name?: string;
        realm_id?: string;
        connection_mode?: string;
      };
      setQbStatusNotice(
        data.connection_mode === "sandbox_demo"
          ? "Switched to Built-in Intuit QuickBooks Sandbox Demo dataset."
          : `Connected QuickBooks Online company: ${data.company_name || "Custom"} (Realm ID: ${data.realm_id || ""}).`,
      );
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "quickbooks", "preview"],
      });
    },
  });

  return (
    <div className="quickbooks-import">
      <h2>QuickBooks Online Migration & Import</h2>
      <p className="muted">
        Reviewed one-way ledger and entity synchronization. Preview mapping,
        verify balance reconciliation in dry-run mode, and post historical books
        through standard accounting controls.
      </p>

      <ErrorNotice
        error={
          previewQuery.error ||
          importDryRun.error ||
          importLive.error ||
          oauthAuthorize.error ||
          connectQuickBooks.error
        }
      />

      <div className="review-summary" style={{ marginBottom: "1.5rem" }}>
        <div
          style={{
            display: "flex",
            gap: "1rem",
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <strong>QuickBooks Connector:</strong>
          {previewMeta?.connection_mode &&
          previewMeta.connection_mode !== "sandbox_demo" ? (
            <Badge>
              CONNECTED ({previewMeta.company_name || "QuickBooks Online"} ·
              Realm {previewMeta.realm_id})
            </Badge>
          ) : previewQuery.data ? (
            <Badge>CONNECTED (SANDBOX READY)</Badge>
          ) : (
            <Badge>SANDBOX READY</Badge>
          )}
        </div>
      </div>

      <div className="panel" style={{ marginBottom: "1.5rem" }}>
        <h3>0. Connect Your QuickBooks Online Account</h3>
        <p className="muted">
          Connect your own QuickBooks Online company via Intuit OAuth 2.0 or by
          entering your QuickBooks Company Realm ID and OAuth Access Token
          (encrypted per workspace), or test using the built-in Sandbox dataset.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            connectQuickBooks.mutate({
              mode: "custom_credentials",
              company_name: String(fd.get("company_name") || ""),
              realm_id: String(fd.get("realm_id") || ""),
              access_token: String(fd.get("access_token") || ""),
              refresh_token: String(fd.get("refresh_token") || ""),
              environment:
                (fd.get("environment") as "sandbox" | "production") ||
                "sandbox",
            });
          }}
        >
          <div className="form-grid">
            <Field label="QuickBooks Company Name">
              <input
                name="company_name"
                placeholder="e.g. Acme Global LLC"
                defaultValue={
                  previewMeta?.connection_mode !== "sandbox_demo"
                    ? previewMeta?.company_name || ""
                    : ""
                }
              />
            </Field>
            <Field label="Intuit Realm ID (Company ID)">
              <input
                name="realm_id"
                placeholder="e.g. 934145291029182"
                defaultValue={
                  previewMeta?.connection_mode !== "sandbox_demo"
                    ? previewMeta?.realm_id || ""
                    : ""
                }
                required
              />
            </Field>
            <Field label="QuickBooks OAuth Access Token">
              <input
                name="access_token"
                type="password"
                placeholder="eyJ... (Fernet-encrypted at rest)"
                required
              />
            </Field>
            <Field label="QuickBooks Environment">
              <select name="environment" defaultValue="sandbox">
                <option value="sandbox">Intuit Sandbox API</option>
                <option value="production">Intuit Production API</option>
              </select>
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
            <Button type="submit" busy={connectQuickBooks.isPending}>
              Connect QuickBooks Company
            </Button>
            {previewMeta?.oauth_configured && (
              <Button
                type="button"
                variant="secondary"
                busy={oauthAuthorize.isPending}
                onClick={() => oauthAuthorize.mutate()}
              >
                Connect via Intuit OAuth 2.0 Redirect
              </Button>
            )}
            {previewMeta?.connection_mode &&
              previewMeta.connection_mode !== "sandbox_demo" && (
                <Button
                  type="button"
                  variant="secondary"
                  busy={connectQuickBooks.isPending}
                  onClick={() =>
                    connectQuickBooks.mutate({ mode: "sandbox_demo" })
                  }
                >
                  Switch to Built-in Sandbox Demo
                </Button>
              )}
          </div>
          {qbStatusNotice && (
            <p
              role="status"
              className="success-text"
              style={{ marginTop: "8px" }}
            >
              {qbStatusNotice}
            </p>
          )}
        </form>
      </div>

      <h3>1. Select Entities for Import</h3>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
          gap: "0.5rem",
          marginBottom: "1rem",
        }}
      >
        {[
          ["Account", "Chart of Accounts"],
          ["Customer", "Customers"],
          ["Vendor", "Vendors"],
          ["JournalEntry", "Journal Entries"],
          ["Invoice", "Invoices"],
          ["Bill", "Bills"],
          ["Payment", "Payments (Customer Receipts)"],
          ["CreditMemo", "Credit Memos (Customer Credits)"],
          ["BillPayment", "Bill Payments (Supplier Settlement)"],
          ["VendorCredit", "Vendor Credits (Applied Credits)"],
        ].map(([key, label]) => (
          <label
            key={key}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              padding: "0.5rem",
              background: "var(--surface-alt)",
              border: "1px solid var(--line)",
              borderRadius: "4px",
              cursor: "pointer",
              color: "var(--ink)",
            }}
          >
            <input
              type="checkbox"
              checked={selectedEntities.includes(key)}
              onChange={() => toggleEntity(key)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>

      <div
        className="form-grid"
        style={{ maxWidth: "300px", marginBottom: "1rem" }}
      >
        <Field label="Cutover Date">
          <input
            type="date"
            value={cutoverDate}
            onChange={(e) => setCutoverDate(e.target.value)}
          />
        </Field>
      </div>

      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1.5rem" }}>
        <Button
          busy={importDryRun.isPending}
          disabled={selectedEntities.length === 0}
          onClick={() => importDryRun.mutate()}
        >
          Run Pre-Import Review (Dry Run)
        </Button>
        <Button
          variant="secondary"
          busy={importLive.isPending}
          disabled={
            selectedEntities.length === 0 ||
            !cutoverDate ||
            importDryRun.isPending ||
            !dryRunData?.reconciled ||
            importLive.isSuccess
          }
          onClick={() => setConfirmLiveImport(true)}
        >
          Confirm & Post to Ledger
        </Button>
      </div>

      {confirmLiveImport && (
        <Modal
          open={true}
          title="Confirm QuickBooks General Ledger Import"
          description="Post reconciled QuickBooks historical books into permanent audited general ledger entries."
          onClose={() => setConfirmLiveImport(false)}
        >
          <p>
            Post these historical QuickBooks records through cutover date{" "}
            <strong>{cutoverDate}</strong> to the general ledger? This action
            creates permanent audited journal entries.
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
              onClick={() => setConfirmLiveImport(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              busy={importLive.isPending}
              onClick={() => {
                importLive.mutate(undefined, {
                  onSuccess: () => setConfirmLiveImport(false),
                });
              }}
            >
              Confirm & Post Journals
            </Button>
          </div>
        </Modal>
      )}

      {dryRunData && (
        <div
          className="panel"
          style={{ padding: "1rem", marginBottom: "1.5rem" }}
        >
          <h4>Pre-Import Reconciliation Review</h4>
          <div
            style={{
              display: "flex",
              gap: "1rem",
              alignItems: "center",
              margin: "0.5rem 0",
            }}
          >
            <strong>Trial Balance:</strong>
            {dryRunData.reconciled ? (
              <Badge>BALANCED / RECONCILED</Badge>
            ) : (
              <Badge>UNBALANCED / UNMAPPED GAPS</Badge>
            )}
            <span>
              Debits: <strong>${dryRunData.total_debit}</strong> | Credits:{" "}
              <strong>${dryRunData.total_credit}</strong>
            </span>
          </div>

          <p style={{ margin: "0.5rem 0", fontSize: "0.9rem" }}>
            Found: {dryRunData.imported_accounts || 0} accounts,{" "}
            {dryRunData.imported_parties || 0} parties,{" "}
            {dryRunData.imported_journals || 0} journals,{" "}
            {dryRunData.imported_invoices || 0} invoices,{" "}
            {dryRunData.imported_bills || 0} bills,{" "}
            {dryRunData.imported_payments || 0} customer payments,{" "}
            {dryRunData.imported_credits || 0} customer credits,{" "}
            {dryRunData.imported_bill_payments || 0} supplier bill payments,{" "}
            {dryRunData.imported_vendor_credits || 0} vendor credits. (
            {dryRunData.skipped_duplicates || 0} existing duplicates skipped).
          </p>

          {unmapped.length > 0 && (
            <div style={{ marginTop: "1rem" }}>
              <h5 style={{ color: "var(--danger, #ff5555)" }}>
                Unmapped Accounts Detected ({unmapped.length})
              </h5>
              <p className="muted" style={{ fontSize: "0.85rem" }}>
                These QuickBooks accounts do not match any active workspace
                accounts. Create or map them in the Chart of Accounts before
                performing live import.
              </p>
              <Table
                caption="Unmapped accounts"
                rows={unmapped}
                rowKey={(u) =>
                  `${u.code || "unknown"}-${u.journal_id || Math.random()}`
                }
                columns={[
                  { label: "QB Code / Ref", render: (u) => u.code || "—" },
                  { label: "Account Name", render: (u) => u.name || "—" },
                  {
                    label: "Referenced In Journal",
                    render: (u) => u.journal_id || "—",
                  },
                ]}
              />
            </div>
          )}
        </div>
      )}

      {liveData && (
        <div className="panel success-box" style={{ padding: "1rem" }}>
          <h4>Import Complete</h4>
          <p role="status" className="success-text">
            Historical books imported successfully:{" "}
            {liveData.imported_journals || 0} journals,{" "}
            {liveData.imported_invoices || 0} invoices,{" "}
            {liveData.imported_bills || 0} bills,{" "}
            {liveData.imported_payments || 0} payments,{" "}
            {liveData.imported_credits || 0} credits.
          </p>
        </div>
      )}
    </div>
  );
}
