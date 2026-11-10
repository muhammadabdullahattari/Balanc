import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Upload, ArrowRight } from "lucide-react";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  NextPage,
  PageHeading,
  Table,
  money,
} from "../components/ui";
import { useBusiness } from "./context";
import type { components } from "../api/generated/schema";

export function Banking() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [action, setAction] =
    useState<components["schemas"]["BankDisposition"]["action"]>("categorize");
  const [splits, setSplits] = useState([
    { account_id: "", amount: "" },
    { account_id: "", amount: "" },
  ]);
  const [bank, setBank] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [upload, setUpload] = useState(false);
  const [csv, setCsv] = useState("");
  const [fileError, setFileError] = useState<unknown>();
  const [importBody, setImportBody] =
    useState<components["schemas"]["CSVImport"]>();
  const [row, setRow] = useState<components["schemas"]["BankRowRead"] | null>(
    null,
  );
  const [command, setCommand] =
    useState<components["schemas"]["BankDisposition"]>();
  const accounts = useQuery({
    queryKey: [...keys.business(b.id), "accounts"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const matches = useQuery({
    queryKey: [...keys.business(b.id), "matches", row?.id],
    enabled: !!row,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/bank-transactions/{row_id}/matches",
          { signal, params: { path: { ...path, row_id: row!.id } } },
        ),
      ),
  });
  const rows = useQuery({
    queryKey: [...keys.business(b.id), "bank", bank, cursor],
    enabled: !!bank,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/bank-transactions", {
          signal,
          params: { path, query: { account_id: bank, cursor, limit: 50 } },
        }),
      ),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };
  const preview = useMutation({
    mutationFn: async (node: HTMLFormElement) => {
      const v = new FormData(node);
      const body: components["schemas"]["CSVPreview"] = {
        account_id: bank,
        content: csv,
        mapping: {
          date_column: String(v.get("date")),
          description_column: String(v.get("description")),
          amount_column: String(v.get("amount")),
          date_format: v.get("format") as "%Y-%m-%d" | "%m/%d/%Y" | "%d/%m/%Y",
        },
      };
      const data = await result(
        api.POST("/api/v1/businesses/{business_id}/bank-imports/preview", {
          params: { path },
          body,
        }),
      );
      setImportBody({ ...body, expected_hash: data.file_hash });
      return data;
    },
  });
  const importing = useMutation({
    mutationFn: (body: components["schemas"]["CSVImport"]) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/bank-imports", {
          params: { path },
          body,
        }),
      ),
    onSuccess: () => {
      setUpload(false);
      setCsv("");
      setImportBody(undefined);
      preview.reset();
      refresh();
    },
  });
  const disposition = useMutation({
    mutationFn: (body: components["schemas"]["BankDisposition"]) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/bank-transactions/{row_id}/disposition",
          { params: { path: { ...path, row_id: row!.id } }, body },
        ),
      ),
    onSuccess: () => {
      setRow(null);
      setCommand(undefined);
      refresh();
    },
  });
  const review = useMutation({
    mutationFn: async (node: HTMLFormElement) => {
      const v = new FormData(node);
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      const body: components["schemas"]["BankDisposition"] = {
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
        expected_row_version: row!.version,
        allow_clearing: action === "transfer" && v.get("clearing") === "on",
        action,
        journal_ids: action === "match" ? v.getAll("journals").map(String) : [],
        peer_id: String(v.get("peer") || "") || null,
        splits:
          action === "split"
            ? splits
            : action === "categorize"
              ? [
                  {
                    account_id: String(v.get("account") || ""),
                    amount: row!.amount.replace(/^-/, ""),
                  },
                ]
              : [],
        reason: String(v.get("reason")),
        target_account_id: ["transfer", "card_payment"].includes(action)
          ? String(v.get("account") || "") || null
          : null,
      };
      const impact = await result(
        api.POST(
          "/api/v1/businesses/{business_id}/bank-transactions/{row_id}/preview",
          { params: { path: { ...path, row_id: row!.id } }, body },
        ),
      );
      setCommand(body);
      return impact;
    },
  });
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="BANKING & REVIEW"
        title="Bank transactions"
        description="Import your statement, review the details, and keep your bank activity connected to your books."
        actions={
          <Button
            disabled={!bank}
            onClick={() => {
              setUpload(true);
              preview.reset();
              importing.reset();
            }}
          >
            <Upload size={16} />
            Import statement
          </Button>
        }
      />
      <div className="filterbar">
        <Field label="Bank or card account">
          <select
            value={bank}
            onChange={(e) => {
              setBank(e.target.value);
              setCursor(undefined);
            }}
          >
            <option value="">Choose account</option>
            {accounts.data?.items
              .filter(
                (a) =>
                  ["asset", "liability"].includes(a.kind) &&
                  !a.control &&
                  a.active,
              )
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </Field>
        <Badge>Review before posting</Badge>
      </div>
      <ErrorNotice error={accounts.error || rows.error} />
      <section className="panel">
        {!bank ? (
          <Empty title="Select an account to begin.">
            Your bank activity stays in review until it is matched or
            categorized.
          </Empty>
        ) : rows.isPending ? (
          <Loading />
        ) : rows.data?.items.length ? (
          <>
            <Table
              caption="Bank transactions"
              rows={rows.data.items}
              rowKey={(r) => r.id}
              columns={[
                { label: "Date", render: (r) => r.accounting_date },
                {
                  label: "Bank description",
                  render: (r) => <strong>{r.description}</strong>,
                },
                { label: "Amount", render: (r) => money(r.amount, b.currency) },
                { label: "Status", render: (r) => <Badge>{r.state}</Badge> },
                {
                  label: "Review",
                  render: (r) => (
                    <Button
                      variant="ghost"
                      disabled={r.state !== "for_review"}
                      onClick={() => {
                        setRow(r);
                        setCommand(undefined);
                        review.reset();
                        disposition.reset();
                      }}
                    >
                      Review <ArrowRight size={14} />
                    </Button>
                  ),
                },
              ]}
            />
            <NextPage
              cursor={rows.data.next_cursor}
              onNext={() => setCursor(rows.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="No bank transactions yet">
            Import a CSV statement to start reviewing transactions.
          </Empty>
        )}
      </section>
      <Modal
        open={upload}
        onClose={() => {
          if (!importing.isPending) setUpload(false);
        }}
        title="Import a bank statement"
        description="Upload a CSV, map its columns, then review the preview before importing."
      >
        <ErrorNotice error={fileError || preview.error || importing.error} />
        {preview.data && importBody ? (
          <>
            <p>
              {preview.data.rows.length} rows · {preview.data.errors.length}{" "}
              issues
            </p>
            {preview.data.errors.length > 0 && (
              <ul>
                {preview.data.errors.map((e, i) => (
                  <li key={i}>{Object.values(e).join(" · ")}</li>
                ))}
              </ul>
            )}
            <Table
              caption="Statement import preview"
              rows={preview.data.rows}
              rowKey={(r) => String(r.position)}
              columns={[
                { label: "Date", render: (r) => r.accounting_date },
                { label: "Description", render: (r) => r.description },
                { label: "Amount", render: (r) => money(r.amount, b.currency) },
              ]}
            />
            <div className="actions">
              <Button variant="secondary" onClick={() => preview.reset()}>
                Back to mapping
              </Button>
              <Button
                disabled={!preview.data.can_import}
                busy={importing.isPending}
                onClick={() => importing.mutate(importBody)}
              >
                Confirm import
              </Button>
            </div>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              preview.mutate(e.currentTarget);
            }}
          >
            <Field label="CSV statement">
              <input
                type="file"
                accept=".csv,text/csv"
                required
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  setFileError(undefined);
                  if (!file) return;
                  if (file.size > 200000) {
                    setFileError(
                      new Error("Choose a CSV smaller than 200 KB."),
                    );
                    setCsv("");
                    return;
                  }
                  setCsv(await file.text());
                }}
              />
            </Field>
            <div className="form-grid">
              <Field label="Date column name">
                <input name="date" defaultValue="date" required />
              </Field>
              <Field label="Description column name">
                <input name="description" defaultValue="description" required />
              </Field>
              <Field label="Amount column name">
                <input name="amount" defaultValue="amount" required />
              </Field>
              <Field label="Date format">
                <select name="format">
                  <option value="%Y-%m-%d">Year-month-day</option>
                  <option value="%m/%d/%Y">Month/day/year</option>
                  <option value="%d/%m/%Y">Day/month/year</option>
                </select>
              </Field>
            </div>
            <Button disabled={!csv} busy={preview.isPending}>
              Preview statement
            </Button>
          </form>
        )}
      </Modal>
      <Modal
        open={!!row}
        onClose={() => {
          if (!disposition.isPending) setRow(null);
        }}
        title="Review transaction"
        description={row ? `${row.accounting_date} · ${row.description}` : ""}
      >
        <ErrorNotice error={review.error || disposition.error} />
        {row && <h2>{money(row.amount, b.currency)}</h2>}
        {command ? (
          <>
            <dl className="review-summary">
              <dt>Action</dt>
              <dd>{command.action}</dd>
              <dt>Reason</dt>
              <dd>{command.reason}</dd>
              <dt>Account</dt>
              <dd>
                {accounts.data?.items.find(
                  (a) => a.id === command.target_account_id,
                )?.name || "Not applicable"}
              </dd>
            </dl>
            {review.data && (
              <Table
                caption="Server-calculated accounting impact"
                rows={review.data.lines}
                rowKey={(l) => l.account_id}
                columns={[
                  {
                    label: "Account",
                    render: (l) =>
                      accounts.data?.items.find((a) => a.id === l.account_id)
                        ?.name || l.account_id,
                  },
                  {
                    label: "Debit",
                    render: (l) => money(l.debit || "0", b.currency),
                  },
                  {
                    label: "Credit",
                    render: (l) => money(l.credit || "0", b.currency),
                  },
                ]}
              />
            )}
            <div className="notice">
              Confirm this reviewed accounting action. Existing posted entries
              cannot be overwritten.
            </div>
            <div className="actions">
              <Button variant="secondary" onClick={() => setCommand(undefined)}>
                Back
              </Button>
              <Button
                permission="post"
                busy={disposition.isPending}
                onClick={() => disposition.mutate(command)}
              >
                {disposition.isError ? "Retry same request" : "Confirm review"}
              </Button>
            </div>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              review.mutate(e.currentTarget);
            }}
          >
            <Field label="Action">
              <select
                name="action"
                value={action}
                onChange={(e) => setAction(e.target.value as typeof action)}
              >
                <option value="categorize">Categorize</option>
                <option value="match">Match existing entries</option>
                <option value="split">Split across accounts</option>
                <option value="transfer">Transfer between accounts</option>
                <option value="card_payment">Credit card payment</option>
                <option value="exclude">Exclude from bookkeeping</option>
              </select>
            </Field>
            <Field label="Account (for categorization)">
              <select name="account">
                <option value="">Choose account</option>
                {accounts.data?.items
                  .filter((a) => a.active)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </select>
            </Field>
            {action === "match" && (
              <fieldset>
                <legend>Suggested existing journals</legend>
                <ErrorNotice error={matches.error} />
                {matches.data?.items.length ? (
                  matches.data.items.map((m) => (
                    <label className="check" key={m.id}>
                      <input type="checkbox" name="journals" value={m.id} />
                      {m.accounting_date} · {m.memo} · {m.source_kind}
                    </label>
                  ))
                ) : (
                  <p className="muted">
                    No existing journal candidates. Record the underlying
                    receipt/payment before matching.
                  </p>
                )}
              </fieldset>
            )}
            {(action === "transfer" || action === "card_payment") && (
              <Field
                label="Other imported bank row reference (optional)"
                hint="Link the opposite bank movement to prevent duplicate posting."
              >
                <input name="peer" />
              </Field>
            )}
            {action === "split" && (
              <fieldset>
                <legend>Split amounts</legend>
                {splits.map((item, i) => (
                  <div className="form-grid" key={i}>
                    <Field label={`Account ${i + 1}`}>
                      <select
                        required
                        value={item.account_id}
                        onChange={(e) =>
                          setSplits((items) =>
                            items.map((v, j) =>
                              j === i
                                ? { ...v, account_id: e.target.value }
                                : v,
                            ),
                          )
                        }
                      >
                        <option value="">Choose account</option>
                        {accounts.data?.items
                          .filter((a) => a.active)
                          .map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.name}
                            </option>
                          ))}
                      </select>
                    </Field>
                    <Field label="Allocation amount">
                      <input
                        required
                        inputMode="decimal"
                        value={item.amount}
                        onChange={(e) =>
                          setSplits((items) =>
                            items.map((v, j) =>
                              j === i ? { ...v, amount: e.target.value } : v,
                            ),
                          )
                        }
                      />
                    </Field>
                    <Button
                      type="button"
                      variant="ghost"
                      disabled={splits.length <= 2}
                      onClick={() =>
                        setSplits((items) => items.filter((_, j) => i !== j))
                      }
                    >
                      Remove split {i + 1}
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="secondary"
                  disabled={splits.length >= 90}
                  onClick={() =>
                    setSplits((items) => [
                      ...items,
                      { account_id: "", amount: "" },
                    ])
                  }
                >
                  Add split
                </Button>
              </fieldset>
            )}
            {action === "transfer" && (
              <label className="check">
                <input name="clearing" type="checkbox" />
                Approve use of the selected clearing account if the other bank
                side has not arrived
              </label>
            )}
            <Field label="Review reason">
              <textarea name="reason" required maxLength={1000} />
            </Field>
            <Button busy={review.isPending}>Preview accounting impact</Button>
          </form>
        )}
      </Modal>
    </>
  );
}
