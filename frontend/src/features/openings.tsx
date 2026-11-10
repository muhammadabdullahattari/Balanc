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
import { today, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Openings() {
  const b = useBusiness(),
    path = { business_id: b.id };
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
