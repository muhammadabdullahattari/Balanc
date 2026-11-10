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
  Modal,
  NextPage,
  money,
} from "../components/ui";
import { today, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
type Command =
  | components["schemas"]["AdvanceCreate"]
  | components["schemas"]["AdvanceApply"];
export function Advances() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const [command, setCommand] = useState<Command>();
  const rows = useQuery({
    queryKey: [...keys.business(b.id), "advances", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/advances", {
          signal,
          params: {
            path,
            query: { as_of: today(b.timezone), cursor, limit: 50 },
          },
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
  const parties = useQuery({
    queryKey: [...keys.business(b.id), "parties"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/parties", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const prepare = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const v = new FormData(form),
        current = await result(
          api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
        );
      const base = {
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
        accounting_date: String(v.get("date")),
        amount: String(v.get("amount")),
      };
      setCommand(
        selected
          ? { ...base, document_id: String(v.get("document")) }
          : {
              ...base,
              party_id: String(v.get("party")),
              direction: v.get("direction") as "customer" | "vendor",
              bank_account_id: String(v.get("bank")),
              deposit_account_id: String(v.get("deposit")),
            },
      );
    },
  });
  const post = useMutation({
    mutationFn: (body: Command) =>
      "document_id" in body
        ? result(
            api.POST(
              "/api/v1/businesses/{business_id}/advances/{advance_id}/applications",
              { params: { path: { ...path, advance_id: selected! } }, body },
            ),
          )
        : result(
            api.POST("/api/v1/businesses/{business_id}/advances", {
              params: { path },
              body,
            }),
          ),
    onSuccess: () => {
      setCommand(undefined);
      setSelected(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  const options = accounts.data?.items
    .filter((a) => a.active)
    .map((a) => (
      <option key={a.id} value={a.id}>
        {a.code} · {a.name}
      </option>
    ));
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="ADVANCES & DEPOSITS"
        title="Advances"
        description="Record customer deposits and supplier advances, then allocate them to issued documents."
      />
      <ErrorNotice
        error={rows.error || accounts.error || parties.error || prepare.error}
      />
      <section className="panel">
        {rows.data?.items.length ? (
          <>
            <Table
              caption="Unapplied advances"
              rows={rows.data.items}
              rowKey={(r) => r.id}
              columns={[
                { label: "Date", render: (r) => r.accounting_date },
                { label: "Direction", render: (r) => r.direction },
                { label: "Amount", render: (r) => money(r.amount, b.currency) },
                {
                  label: "Unapplied",
                  render: (r) => money(r.unapplied, b.currency),
                },
                {
                  label: "Action",
                  render: (r) => (
                    <Button variant="ghost" onClick={() => setSelected(r.id)}>
                      Apply to document
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
          <Empty title="No advances recorded." />
        )}
      </section>
      <section className="panel settings-section">
        <h2>{selected ? "Apply an advance" : "Record an advance"}</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            post.reset();
            prepare.mutate(e.currentTarget);
          }}
        >
          {selected ? (
            <>
              <Field label="Issued document reference">
                <input required name="document" />
              </Field>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setSelected(undefined)}
              >
                Cancel allocation
              </Button>
            </>
          ) : (
            <>
              <Field label="Contact">
                <select name="party" required>
                  <option value="">Choose contact</option>
                  {parties.data?.items.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Direction">
                <select name="direction">
                  <option value="customer">Customer deposit received</option>
                  <option value="vendor">Supplier advance paid</option>
                </select>
              </Field>
              <Field label="Bank account">
                <select required name="bank">
                  <option value="">Choose account</option>
                  {options}
                </select>
              </Field>
              <Field label="Deposit control account">
                <select required name="deposit">
                  <option value="">Choose account</option>
                  {options}
                </select>
              </Field>
            </>
          )}
          <div className="form-grid">
            <Field label="Accounting date">
              <input
                required
                type="date"
                name="date"
                defaultValue={today(b.timezone)}
              />
            </Field>
            <Field label="Amount">
              <input
                required
                name="amount"
                inputMode="decimal"
                pattern="[0-9]+(\.[0-9]{1,6})?"
              />
            </Field>
          </div>
          <Button busy={prepare.isPending}>Review entry</Button>
        </form>
      </section>
      <Modal
        open={!!command}
        onClose={() => {
          if (!post.isPending) setCommand(undefined);
        }}
        title="Approve advance entry"
        description="Record a completed movement or allocation. This action does not transfer money."
      >
        <ErrorNotice error={post.error} />
        {command && (
          <>
            <dl className="review-summary">
              <dt>Date</dt>
              <dd>{command.accounting_date}</dd>
              <dt>Amount</dt>
              <dd>{money(command.amount, b.currency)}</dd>
              <dt>Action</dt>
              <dd>
                {"document_id" in command
                  ? "Apply to document"
                  : "Record deposit"}
              </dd>
            </dl>
            <Button
              permission="post"
              busy={post.isPending}
              onClick={() => post.mutate(command)}
            >
              {post.isError ? "Retry same request" : "Approve entry"}
            </Button>
          </>
        )}
      </Modal>
    </>
  );
}
