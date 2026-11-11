import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, result, keys, queryClient } from "../api/runtime";
import {
  Button,
  Field,
  PageHeading,
  Table,
  Empty,
  ErrorNotice,
  NextPage,
  money,
} from "../components/ui";
import { today, useAccess, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Openings() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const access = useAccess();
  const [baseline, setBaseline] = useState([
    { code: "", amount: "" },
    { code: "", amount: "" },
  ]);
  const [content, setContent] = useState("");
  const [fileError, setFileError] = useState<unknown>();
  const [cursor, setCursor] = useState<string>();
  const [command, setCommand] =
    useState<components["schemas"]["ImportApply"]>();
  const rows = useQuery({
    queryKey: [...keys.business(b.id), "openings", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/opening-balances", {
          signal,
          params: { path, query: { limit: 50, cursor } },
        }),
      ),
  });
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
  const preview = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const v = new FormData(form);
      if (new Set(baseline.map((row) => row.code)).size !== baseline.length)
        throw new Error("Each baseline account must appear only once.");
      const body: components["schemas"]["ImportPreview"] = {
        mode: "opening",
        content,
        cutover_date: String(v.get("date")),
        opening_equity_account_code: String(v.get("equity")),
        review_reason: String(v.get("reason")),
        columns: {},
        expected_trial: Object.fromEntries(
          baseline.map((row) => [row.code, row.amount]),
        ),
      };
      const data = await result(
        api.POST("/api/v1/businesses/{business_id}/imports/preview", {
          params: { path },
          body,
        }),
      );
      setCommand({
        ...body,
        expected_version: data.business_version,
        preview_hash: data.preview_hash,
        idempotency_key: crypto.randomUUID(),
      });
      return data;
    },
  });
  const apply = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/imports/apply", {
          params: { path },
          body: command!,
        }),
      ),
    onSuccess: () => {
      setCommand(undefined);
      setContent("");
      preview.reset();
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="MIGRATE YOUR BOOKS"
        title="Opening balances"
        description="Bring customer, supplier and loan balances forward without replaying historic cash."
      />
      <ErrorNotice
        error={
          rows.error ||
          accounts.error ||
          preview.error ||
          apply.error ||
          fileError
        }
      />
      <section className="panel settings-section">
        <h2>Accountant-reviewed opening import</h2>
        <p className="muted">
          Opening imports require books with no posted journals. CSV columns:
          kind, party_id, reference, original_date, due_date, amount,
          control_account_code. Preserve original dates and use existing contact
          references.
        </p>
        {command && preview.data ? (
          <>
            <p>Review policy: {preview.data.policy}</p>
            <p>
              {preview.data.commands.length} proposed records ·{" "}
              {preview.data.errors.length} errors
            </p>
            {preview.data.errors.map((e, i) => (
              <p role="alert" key={i}>
                {Object.values(e).join(" · ")}
              </p>
            ))}
            <div className="notice">
              No records have been posted. The server validates the complete
              import and selected opening-equity account.
            </div>
            <Button
              variant="secondary"
              disabled={apply.isPending}
              onClick={() => {
                setCommand(undefined);
                preview.reset();
              }}
            >
              Back to edit
            </Button>
            <Button
              permission="post"
              busy={apply.isPending}
              disabled={!preview.data.can_apply}
              onClick={() => apply.mutate()}
            >
              {apply.isError ? "Retry same import" : "Approve opening balances"}
            </Button>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              apply.reset();
              preview.mutate(e.currentTarget);
            }}
          >
            <Field label="Opening balances CSV">
              <input
                type="file"
                accept=".csv,text/csv"
                required
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  if (file.size > 200000) {
                    setFileError(new Error("CSV must be smaller than 200 KB."));
                    return;
                  }
                  setFileError(undefined);
                  setContent(await file.text());
                }}
              />
            </Field>
            <Field label="Cutover date">
              <input
                name="date"
                type="date"
                required
                defaultValue={today(b.timezone)}
              />
            </Field>
            <Field label="Opening-equity account">
              <select name="equity" required>
                <option value="">Choose equity account</option>
                {accounts.data?.items
                  .filter((a) => a.active && a.kind === "equity")
                  .map((a) => (
                    <option key={a.id} value={a.code}>
                      {a.code} · {a.name}
                    </option>
                  ))}
              </select>
            </Field>
            <fieldset>
              <legend>Accountant-approved legacy trial balance</legend>
              <p className="muted">
                Supply signed debit-minus-credit closing balances, including
                opening equity. Accounts omitted here are expected to be zero;
                the server checks every account and rolls back a mismatch.
              </p>
              {baseline.map((row, i) => (
                <div className="form-grid" key={i}>
                  <Field label={`Baseline account ${i + 1}`}>
                    <select
                      required
                      value={row.code}
                      onChange={(e) =>
                        setBaseline((rows) =>
                          rows.map((r, j) =>
                            j === i ? { ...r, code: e.target.value } : r,
                          ),
                        )
                      }
                    >
                      <option value="">Choose account</option>
                      {accounts.data?.items.map((a) => (
                        <option key={a.id} value={a.code}>
                          {a.code} · {a.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label={`Signed balance ${i + 1}`}>
                    <input
                      required
                      inputMode="decimal"
                      pattern="-?[0-9]+(\.[0-9]{1,6})?"
                      value={row.amount}
                      onChange={(e) =>
                        setBaseline((rows) =>
                          rows.map((r, j) =>
                            j === i ? { ...r, amount: e.target.value } : r,
                          ),
                        )
                      }
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    disabled={baseline.length <= 2}
                    onClick={() =>
                      setBaseline((rows) => rows.filter((_, j) => j !== i))
                    }
                  >
                    Remove baseline {i + 1}
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="secondary"
                disabled={baseline.length >= 1000}
                onClick={() =>
                  setBaseline((rows) => [...rows, { code: "", amount: "" }])
                }
              >
                Add baseline account
              </Button>
            </fieldset>
            <Field label="Accountant review reason">
              <textarea name="reason" required maxLength={1000} />
            </Field>
            <Button busy={preview.isPending} disabled={!content}>
              Validate & preview import
            </Button>
          </form>
        )}
        {apply.isSuccess && <p role="status">Opening balances imported.</p>}
      </section>
      {(access?.role === "owner" || access?.role === "accountant") && (
        <ParallelComparison key={b.id} />
      )}
      <section className="panel">
        {rows.data?.items.length ? (
          <>
            <Table
              caption="Reviewed opening balances"
              rows={rows.data.items}
              rowKey={(r) => r.id}
              columns={[
                { label: "Reference", render: (r) => r.reference },
                { label: "Kind", render: (r) => r.kind },
                { label: "Original date", render: (r) => r.original_date },
                { label: "Due", render: (r) => r.due_date },
                { label: "Cutover", render: (r) => r.cutover_date },
                { label: "Amount", render: (r) => money(r.amount, b.currency) },
                { label: "Review reason", render: (r) => r.review_reason },
              ]}
            />
            <NextPage
              cursor={rows.data.next_cursor}
              onNext={() => setCursor(rows.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="No opening balances imported." />
        )}
      </section>
    </>
  );
}

function ParallelComparison() {
  const business = useBusiness();
  const path = { business_id: business.id };
  const [cursor, setCursor] = useState<string>();
  const [batchId, setBatchId] = useState("");
  const batches = useQuery({
    queryKey: [...keys.business(business.id), "import-batches", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/imports", {
          signal,
          params: { path, query: { limit: 50, cursor } },
        }),
      ),
  });
  const compare = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const fields = new FormData(form);
      const rows = String(fields.get("trial"))
        .trim()
        .split(/\r?\n/)
        .map((line) => line.split("\t").map((value) => value.trim()));
      if (rows[0]?.join("\t") === "code\tbalance") rows.shift();
      if (rows.some((row) => row.length !== 2 || !row[0] || !row[1])) {
        throw new Error(
          "Paste one account code and signed balance per row, separated by a tab.",
        );
      }
      const codes = new Set(rows.map(([code]) => code));
      if (!rows.length || codes.size !== rows.length || rows.length > 1000) {
        throw new Error("Supply between 1 and 1,000 unique account codes.");
      }
      return result(
        api.POST(
          "/api/v1/businesses/{business_id}/imports/{batch_id}/parallel-comparison",
          {
            params: { path: { ...path, batch_id: batchId } },
            body: {
              as_of: String(fields.get("as_of")),
              expected_trial: Object.fromEntries(rows),
            },
          },
        ),
      );
    },
  });
  const selected = batches.data?.items.find((batch) => batch.id === batchId);
  const report = compare.data;
  return (
    <section className="panel settings-section">
      <h2>Parallel-run trial balance</h2>
      <p className="muted">
        Compare the source books and LedgerPilot on the same date. Use debit
        minus credit for each account balance. Omitted accounts are treated as
        zero.
      </p>
      <ErrorNotice error={batches.error || compare.error} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          compare.mutate(event.currentTarget);
        }}
      >
        <Field label="Financial import batch">
          <select
            required
            value={batchId}
            disabled={compare.isPending}
            onChange={(event) => {
              setBatchId(event.target.value);
              compare.reset();
            }}
          >
            <option value="">Choose a financial import</option>
            {batches.data?.items
              .filter((batch) => batch.mode !== "chart")
              .map((batch) => (
                <option key={batch.id} value={batch.id}>
                  {batch.cutover_date} · {batch.mode} · {batch.id.slice(0, 8)}
                </option>
              ))}
          </select>
        </Field>
        <NextPage
          cursor={batches.data?.next_cursor}
          onNext={() => {
            setBatchId("");
            compare.reset();
            setCursor(batches.data?.next_cursor || undefined);
          }}
          onReset={() => {
            setBatchId("");
            compare.reset();
            setCursor(undefined);
          }}
        />
        <Field label="Comparison date">
          <input
            name="as_of"
            type="date"
            required
            min={selected?.cutover_date}
          />
        </Field>
        <Field label="Legacy account codes and signed balances">
          <textarea
            name="trial"
            required
            rows={5}
            maxLength={100000}
            placeholder="Paste two tab-separated spreadsheet columns"
          />
        </Field>
        <Button busy={compare.isPending} disabled={!selected}>
          Compare trial balances
        </Button>
      </form>
      {report?.items && (
        <>
          <p role="status">
            {report.balanced
              ? "Account balances match."
              : "Account differences need review."}{" "}
            {report.controls?.balanced
              ? "Ledger control accounts reconcile."
              : "Ledger control accounts need review."}{" "}
            Compared as of {report.as_of}.
          </p>
          <Table
            caption="Parallel-run account balances"
            rows={report.items}
            rowKey={(row) => row.account_id}
            columns={[
              { label: "Account code", render: (row) => row.code },
              {
                label: "Legacy",
                render: (row) => money(row.expected, business.currency),
              },
              {
                label: "LedgerPilot",
                render: (row) => money(row.actual, business.currency),
              },
              {
                label: "Difference",
                render: (row) => money(row.difference, business.currency),
              },
            ]}
          />
        </>
      )}
    </section>
  );
}
