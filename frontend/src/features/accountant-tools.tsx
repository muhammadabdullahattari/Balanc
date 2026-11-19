import { AssetRegister } from "./asset-register";
import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { Button, ErrorNotice, Field } from "../components/ui";
import { today, useAccess, useBusiness } from "./context";
import { resolveBookQuestion } from "./book-question";
import type { components } from "../api/generated/schema";

export function AskBooks() {
  const b = useBusiness();
  const [previous, setPrevious] = useState("");
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
    onSuccess: (answer, question) => {
      if (answer.report_ids.length) setPrevious(question);
    },
  });
  return (
    <details className="panel">
      <summary>Ask about your books</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!ask.isPending)
            ask.mutate(
              resolveBookQuestion(
                String(new FormData(e.currentTarget).get("question")),
                previous,
              ),
            );
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
        {previous && <p>Last answered question: {previous}</p>}
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
  const formRef = useRef<HTMLFormElement>(null);
  const quote = useMutation({
    mutationFn: async (manual: boolean) => {
      const form = formRef.current!;
      const values = new FormData(form);
      const common = {
        day: String(values.get("date")),
        currency: String(values.get("foreign")) as
          "USD" | "GBP" | "PKR" | "AED" | "EUR",
      };
      return manual
        ? result(
            api.POST(
              "/api/v1/businesses/{business_id}/accounting/fx-rates/override",
              {
                params: { path },
                body: {
                  ...common,
                  rate: String(values.get("rate")),
                  source: String(values.get("source")),
                },
              },
            ),
          )
        : result(
            api.POST(
              "/api/v1/businesses/{business_id}/accounting/fx-rates/lookup",
              { params: { path }, body: common },
            ),
          );
    },
    onSuccess: (data) => {
      const form = formRef.current;
      if (!form) return;
      const rate = form.elements.namedItem("rate"),
        source = form.elements.namedItem("source");
      if (
        rate instanceof HTMLInputElement &&
        source instanceof HTMLInputElement
      ) {
        rate.value = data.rate;
        source.value = data.source;
        prepare.reset();
        post.reset();
      }
    },
  });
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
      const entry =
        kind === "registered"
          ? await result(
              api.POST(
                "/api/v1/businesses/{business_id}/accounting/assets/{asset_id}/installment-preview",
                {
                  params: { path: { ...path, asset_id: str("asset") } },
                  body: {
                    number: Number(str("number")),
                    debit_account: str("debit"),
                    credit_account: str("credit"),
                  },
                },
              ),
            )
          : kind === "revaluation"
            ? await result(
                api.POST(
                  "/api/v1/businesses/{business_id}/accounting/revaluation-preview",
                  {
                    params: { path },
                    body: {
                      period_end: str("date"),
                      currency: str("foreign") as
                        "USD" | "GBP" | "EUR" | "AED" | "PKR",
                      foreign_units: str("amount"),
                      carrying_balance: str("carrying"),
                      closing_rate: str("rate"),
                      rate_source: str("source"),
                      monetary_account: str("debit"),
                      gain_account: str("credit"),
                      loss_account: str("loss"),
                    },
                  },
                ),
              )
            : await result(
                api.POST(
                  "/api/v1/businesses/{business_id}/accounting/entry-preview",
                  {
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
                  },
                ),
              );
      const business = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      return {
        ...entry,
        expected_version: business.version,
        idempotency_key:
          kind === "registered"
            ? `asset.${str("asset")}.${str("number")}`
            : kind === "revaluation"
              ? `fx.${str("date")}.${str("foreign")}.${str("debit")}`
              : crypto.randomUUID(),
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
    <>
      <AssetRegister />
      <details className="panel">
        <summary>Prepare an accounting entry</summary>
        <form
          ref={formRef}
          onChange={() => {
            prepare.reset();
            post.reset();
          }}
          onSubmit={(e) => {
            e.preventDefault();
            prepare.mutate(new FormData(e.currentTarget));
          }}
        >
          <fieldset
            disabled={prepare.isPending || post.isPending || quote.isPending}
          >
            <Field label="Entry type">
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="registered">Registered asset installment</option>
                <option value="accrual">Accrual</option>
                <option value="depreciation">Straight-line depreciation</option>
                <option value="fx">Foreign currency conversion</option>
                <option value="revaluation">Period-end FX adjustment</option>
              </select>
            </Field>
            {kind === "registered" ? (
              <>
                <Field label="Asset reference">
                  <input name="asset" required />
                </Field>
                <Field label="Schedule installment">
                  <input
                    name="number"
                    type="number"
                    min="1"
                    max="1200"
                    required
                  />
                </Field>
              </>
            ) : (
              <>
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
                      : kind === "fx" || kind === "revaluation"
                        ? "Foreign amount"
                        : "Accrued amount"
                  }
                >
                  <input required name="amount" inputMode="decimal" />
                </Field>
              </>
            )}
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
            {(kind === "fx" || kind === "revaluation") && (
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
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => quote.mutate(false)}
                >
                  Look up closing rate
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => quote.mutate(true)}
                >
                  Save manual rate
                </Button>
                <ErrorNotice error={quote.error} />
                <Field label="Exchange rate source">
                  <input required name="source" maxLength={120} />
                </Field>
              </>
            )}
            {kind === "revaluation" && (
              <>
                <Field label="Current carrying balance">
                  <input required name="carrying" inputMode="decimal" />
                </Field>
                <Field label="Unrealized loss account">
                  <select name="loss" required defaultValue="">
                    <option value="">Choose an expense account</option>
                    {accounts
                      .filter((a) => a.active)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                  </select>
                </Field>
              </>
            )}
            {["debit", "credit"].map((side) => (
              <Field
                key={side}
                label={
                  kind === "revaluation"
                    ? side === "debit"
                      ? "Monetary account"
                      : "Unrealized gain account"
                    : side === "debit"
                      ? "Debit account"
                      : "Credit account"
                }
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
    </>
  );
}
