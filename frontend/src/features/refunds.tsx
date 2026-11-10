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
export function Refunds() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] =
    useState<components["schemas"]["PaymentRead"]>();
  const [command, setCommand] =
    useState<components["schemas"]["PaymentCreate"]>();
  const payments = useQuery({
    queryKey: [...keys.business(b.id), "payments", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/payments", {
          signal,
          params: { path, query: { cursor, limit: 50 } },
        }),
      ),
  });
  const prepare = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const v = new FormData(form),
        current = await result(
          api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
        );
      setCommand({
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
        kind:
          selected!.kind === "receipt" ? "customer_refund" : "vendor_refund",
        party_id: selected!.party_id,
        control_account_id: selected!.control_account_id,
        bank_account_id: selected!.bank_account_id,
        original_payment_id: selected!.id,
        accounting_date: String(v.get("date")),
        amount: String(v.get("amount")),
        fee_amount: "0",
        allocations: [
          {
            document_id: String(v.get("document")),
            credit_note_id: String(v.get("credit")),
            amount: String(v.get("amount")),
          },
        ],
      });
    },
  });
  const post = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/payments", {
          params: { path },
          body: command!,
        }),
      ),
    onSuccess: () => {
      setSelected(undefined);
      setCommand(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="PAYMENTS & REFUNDS"
        title="Payments and refunds"
        description="Refunds link back to an original settlement, issued credit note and document."
      />
      <ErrorNotice error={payments.error} />
      <section className="panel">
        {payments.data?.items.length ? (
          <>
            <Table
              caption="Payment history"
              rows={payments.data.items}
              rowKey={(p) => p.id}
              columns={[
                { label: "Date", render: (p) => p.accounting_date },
                { label: "Type", render: (p) => p.kind },
                { label: "Amount", render: (p) => money(p.total, b.currency) },
                {
                  label: "Action",
                  render: (p) =>
                    ["receipt", "payment"].includes(p.kind) ? (
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setSelected(p);
                          setCommand(undefined);
                          post.reset();
                        }}
                      >
                        Record refund
                      </Button>
                    ) : (
                      "Linked refund"
                    ),
                },
              ]}
            />
            <NextPage
              cursor={payments.data.next_cursor}
              onNext={() => setCursor(payments.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="No recorded settlements." />
        )}
      </section>
      <Modal
        open={!!selected}
        onClose={() => {
          if (!post.isPending) setSelected(undefined);
        }}
        title="Record a completed refund"
        description="This records a refund already made; no money is transferred."
      >
        <ErrorNotice error={prepare.error || post.error} />
        {command ? (
          <>
            <dl className="review-summary">
              <dt>Original payment</dt>
              <dd>{command.original_payment_id}</dd>
              <dt>Refund amount</dt>
              <dd>{money(command.amount, b.currency)}</dd>
              <dt>Date</dt>
              <dd>{command.accounting_date}</dd>
            </dl>
            <Button
              variant="secondary"
              disabled={post.isPending}
              onClick={() => setCommand(undefined)}
            >
              Edit
            </Button>
            <Button
              permission="post"
              busy={post.isPending}
              onClick={() => post.mutate()}
            >
              {post.isError ? "Retry same refund" : "Approve refund record"}
            </Button>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              prepare.mutate(e.currentTarget);
            }}
          >
            <Field label="Original document">
              <select required name="document">
                <option value="">Choose paid document</option>
                {selected?.allocations.map((a) => (
                  <option key={a.document_id} value={a.document_id}>
                    {a.document_id} · {money(a.amount, b.currency)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Issued credit-note reference">
              <input name="credit" required />
            </Field>
            <Field label="Refund date">
              <input
                name="date"
                type="date"
                required
                defaultValue={today(b.timezone)}
              />
            </Field>
            <Field label="Refund amount">
              <input
                name="amount"
                required
                inputMode="decimal"
                pattern="[0-9]+(\.[0-9]{1,6})?"
              />
            </Field>
            <Button busy={prepare.isPending}>Review refund</Button>
          </form>
        )}
      </Modal>
    </>
  );
}
