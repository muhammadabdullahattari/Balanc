import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { Button, ErrorNotice, Field } from "../components/ui";
import { today, useAccess, useBusiness } from "./context";
import type { components } from "../api/generated/schema";

export function AskBooks() {
  const b = useBusiness();
  const ask = useMutation({
    mutationFn: (question: string) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/insights/question", {
          params: { path: { business_id: b.id } },
          body: {
            question,
            as_of: today(b.timezone),
            basis: b.basis === "cash" ? "cash" : "accrual",
          },
        }),
      ),
  });
  return (
    <details className="panel">
      <summary>Ask about your books</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask.mutate(String(new FormData(e.currentTarget).get("question")));
        }}
      >
        <Field label="Accounting question">
          <input
            required
            name="question"
            maxLength={240}
            placeholder="What was our net profit last month?"
            onChange={() => ask.reset()}
          />
        </Field>
        <Button busy={ask.isPending}>Ask</Button>
      </form>
      <ErrorNotice error={ask.error} />
      {ask.data && (
        <div role="status">
          <p>{ask.data.answer}</p>
          {ask.data.report_ids.map((id, i) => (
            <a
              key={id}
              href={`/api/v1/businesses/${b.id}/reports/history/${id}`}
              target="_blank"
              rel="noreferrer"
            >
              Source report {i + 1}{" "}
            </a>
          ))}
        </div>
      )}
    </details>
  );
}

type Accounts = Pick<
  components["schemas"]["AccountRead"],
  "id" | "name" | "active"
>[];
export function EntryTools({ accounts }: { accounts: Accounts }) {
  const b = useBusiness(),
    access = useAccess();
  const [kind, setKind] = useState("accrual");
  const path = { business_id: b.id };
  const prepare = useMutation({
    mutationFn: async (form: FormData) => {
      const str = (key: string) => String(form.get(key) ?? "");
      const fields = {
        amount: str("amount"),
        accounting_date: str("date"),
        reference: str("reference"),
        debit_account: str("debit"),
        credit_account: str("credit"),
      };
      const entry = await result(
        api.POST("/api/v1/businesses/{business_id}/accounting/entry-preview", {
          params: { path },
          body:
            kind === "depreciation"
              ? {
                  ...fields,
                  kind: "depreciation",
                  residual: str("residual"),
                  months: Number(str("months")),
                  installment: Number(str("installment")),
                }
              : kind === "fx"
                ? {
                    ...fields,
                    kind: "fx",
                    source_currency: str("foreign") as
                      "USD" | "AED" | "PKR" | "GBP" | "EUR",
                    rate: str("rate"),
                    rate_source: str("source"),
                  }
                : { ...fields, kind: "accrual" },
        }),
      );
      const business = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      return {
        ...entry,
        expected_version: business.version,
        idempotency_key: crypto.randomUUID(),
      };
    },
  });
  const post = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/journals", {
          params: { path },
          body: prepare.data!,
        }),
      ),
    onSuccess: () => {
      prepare.reset();
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  if (!access || !(access.can_post || access.role === "accountant"))
    return null;
  return (
    <details className="panel">
      <summary>Prepare an accounting entry</summary>
      <form
        onChange={() => {
          prepare.reset();
          post.reset();
        }}
        onSubmit={(e) => {
          e.preventDefault();
          prepare.mutate(new FormData(e.currentTarget));
        }}
      >
        <fieldset disabled={prepare.isPending || post.isPending}>
          <Field label="Entry type">
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="accrual">Accrual</option>
              <option value="depreciation">Straight-line depreciation</option>
              <option value="fx">Foreign currency conversion</option>
            </select>
          </Field>
          <Field label="Source reference">
            <input required name="reference" maxLength={160} />
          </Field>
          <Field label="Accounting date">
            <input
              required
              type="date"
              name="date"
              defaultValue={today(b.timezone)}
            />
          </Field>
          <Field
            label={
              kind === "depreciation"
                ? "Asset acquisition cost"
                : kind === "fx"
                  ? "Foreign amount"
                  : "Accrued amount"
            }
          >
            <input required name="amount" inputMode="decimal" />
          </Field>
          {kind === "depreciation" && (
            <>
              <Field label="Residual value">
                <input required name="residual" defaultValue="0" />
              </Field>
              <Field label="Useful life in months">
                <input
                  required
                  name="months"
                  type="number"
                  min="1"
                  max="1200"
                />
              </Field>
              <Field label="Installment number">
                <input
                  required
                  name="installment"
                  type="number"
                  min="1"
                  max="1200"
                />
              </Field>
            </>
          )}
          {kind === "fx" && (
            <>
              <Field label="Transaction currency">
                <select name="foreign">
                  {["USD", "PKR", "GBP", "AED", "EUR"]
                    .filter((c) => c !== b.currency)
                    .map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                </select>
              </Field>
              <Field label={`${b.currency} per unit of foreign currency`}>
                <input required name="rate" inputMode="decimal" />
              </Field>
              <Field label="Exchange rate source">
                <input required name="source" maxLength={120} />
              </Field>
            </>
          )}
          {["debit", "credit"].map((side) => (
            <Field
              key={side}
              label={side === "debit" ? "Debit account" : "Credit account"}
            >
              <select name={side} required defaultValue="">
                <option value="">Choose an account</option>
                {accounts
                  .filter((a) => a.active)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </select>
            </Field>
          ))}
          <Button busy={prepare.isPending}>Preview entry</Button>
        </fieldset>
      </form>
      <ErrorNotice error={prepare.error || post.error} />
      {prepare.data && (
        <section>
          <p>{prepare.data.memo}</p>
          {prepare.data.lines.map((l, i) => (
            <p key={l.account_id}>
              {i === 0 ? "Debit" : "Credit"}{" "}
              {accounts.find((a) => a.id === l.account_id)?.name}:{" "}
              {i === 0 ? l.debit : l.credit} {prepare.data.currency}
            </p>
          ))}
          <Button busy={post.isPending} onClick={() => post.mutate()}>
            Approve and post entry
          </Button>
        </section>
      )}
      {post.data && <p role="status">Posted journal {post.data.id}.</p>}
    </details>
  );
}
