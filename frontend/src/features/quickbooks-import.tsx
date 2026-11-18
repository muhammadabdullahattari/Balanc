import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError, keys, queryClient, result } from "../api/runtime";
import { Badge, Button, ErrorNotice, Field, Table } from "../components/ui";
import { today, useAccess, useBusiness } from "./context";

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

  const access = useAccess();
  const connect = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/quickbooks/authorize", {
          params: { path },
        }),
      ),
    onSuccess: (data) => {
      if (data.url) window.location.assign(data.url);
    },
  });
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

  if (!previewQuery.data) {
    const disconnected =
      previewQuery.error instanceof ApiError &&
      previewQuery.error.code === "ERR_QUICKBOOKS_DISCONNECTED";
    return (
      <section>
        <h2>QuickBooks import</h2>
        {previewQuery.isPending && (
          <p role="status">Checking the QuickBooks connection…</p>
        )}
        {disconnected && (
          <p>Connect your QuickBooks company to preview records for import.</p>
        )}
        {disconnected &&
          (access?.role === "owner" ? (
            <Button busy={connect.isPending} onClick={() => connect.mutate()}>
              Connect QuickBooks
            </Button>
          ) : (
            <p>Ask your workspace owner to connect QuickBooks.</p>
          ))}
        <ErrorNotice
          error={connect.error || (!disconnected && previewQuery.error)}
        />
      </section>
    );
  }
  return (
    <div className="quickbooks-import">
      <h2>Import from QuickBooks</h2>
      <p className="muted">
        Choose the records to bring into this workspace. Review the totals
        before posting to the ledger.
      </p>

      <ErrorNotice
        error={previewQuery.error || importDryRun.error || importLive.error}
      />

      <div className="review-summary" style={{ marginBottom: "1.5rem" }}>
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "1rem",
            alignItems: "center",
          }}
        >
          <strong>Sandbox Connector:</strong>
          <Badge>CONNECTED</Badge>
        </div>
      </div>

      <fieldset className="import-options">
        <legend>Records to import</legend>
        <div className="import-grid">
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
            <label key={key} className="import-option">
              <input
                type="checkbox"
                disabled={importDryRun.isPending || importLive.isPending}
                checked={selectedEntities.includes(key)}
                onChange={() => toggleEntity(key)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div
        className="form-grid"
        style={{ maxWidth: "300px", marginBottom: "1rem" }}
      >
        <Field label="Cutover Date">
          <input
            type="date"
            value={cutoverDate}
            disabled={importDryRun.isPending || importLive.isPending}
            onChange={(e) => {
              importDryRun.reset();
              importLive.reset();
              setCutoverDate(e.target.value);
            }}
          />
        </Field>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "0.75rem",
          marginBottom: "1.5rem",
        }}
      >
        <Button
          busy={importDryRun.isPending}
          disabled={
            !cutoverDate ||
            selectedEntities.length === 0 ||
            importLive.isPending
          }
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
          permission="post"
          onClick={() => {
            if (
              window.confirm(
                "Post these historical QuickBooks records to the general ledger? This action creates permanent audited journal entries.",
              )
            ) {
              importLive.mutate();
            }
          }}
        >
          Confirm & Post to Ledger
        </Button>
      </div>

      {dryRunData && (
        <div
          className="panel"
          style={{ padding: "1rem", marginBottom: "1.5rem" }}
        >
          <h4>Pre-Import Reconciliation Review</h4>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
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
                rowKey={(u, index) =>
                  `${u.code || "unknown"}-${u.journal_id || index}`
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
