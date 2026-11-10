import { LookupMore } from "./lookup-more";
import { useState, useRef } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus, Trash2, ArrowUpRight } from "lucide-react";
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
import { today, useBusiness } from "./context";
import type { components } from "../api/generated/schema";

type Document = components["schemas"]["DocumentRead"];
type Draft = {
  subtype:
    "invoice" | "bill" | "sales_receipt" | "customer_credit" | "vendor_credit";
  bank_account_id: string;
  original_id: string;
  number: string;
  party_id: string;
  accounting_date: string;
  due_date: string;
  control_account_id: string;
  lines: {
    account_id: string;
    description: string;
    amount: string;
    tax_amount: string;
    tax_account_id: string;
    tax_code: string;
  }[];
};
export function Trade({ kind }: { kind: "invoice" | "bill" }) {
  const b = useBusiness();
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState<string | undefined>();
  const [selected, setSelected] = useState<Document | null>(null);
  const [payload, setPayload] =
    useState<components["schemas"]["DocumentCreate"]>();
  const [paymentBody, setPaymentBody] =
    useState<components["schemas"]["PaymentCreate"]>();
  const [documentKind, setDocumentKind] =
    useState<components["schemas"]["DocumentRead"]["kind"]>(kind);
  const [asOf, setAsOf] = useState(today(b.timezone));
  const docs = useQuery({
    queryKey: [...keys.business(b.id), "documents", documentKind, asOf, cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/trade-documents", {
          signal,
          params: {
            path: { business_id: b.id },
            query: { as_of: asOf, kind: documentKind, cursor, limit: 50 },
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
          params: { path: { business_id: b.id }, query: { limit: 200 } },
        }),
      ),
  });
  const parties = useQuery({
    queryKey: [...keys.business(b.id), "parties"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/parties", {
          signal,
          params: { path: { business_id: b.id }, query: { limit: 200 } },
        }),
      ),
  });
  const form = useForm<Draft>({
    defaultValues: {
      subtype: kind,
      bank_account_id: "",
      original_id: "",
      number: "",
      party_id: "",
      accounting_date: today(b.timezone),
      due_date: today(b.timezone),
      control_account_id: "",
      lines: [
        {
          account_id: "",
          description: "",
          amount: "",
          tax_amount: "0",
          tax_account_id: "",
          tax_code: "",
        },
      ],
    },
  });
  const lines = useFieldArray({ control: form.control, name: "lines" });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };
  const preview = useMutation({
    mutationFn: async (values: Draft) => {
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", {
          params: { path: { business_id: b.id } },
        }),
      );
      const { subtype, ...documentValues } = values;
      const body = {
        ...documentValues,
        bank_account_id: values.bank_account_id || null,
        original_id: values.original_id || null,
        lines: values.lines.map((line) => ({
          ...line,
          tax_account_id: line.tax_account_id || null,
          tax_code: line.tax_code || null,
        })),
        kind: subtype,
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
      };
      const impact = await result(
        api.POST("/api/v1/businesses/{business_id}/trade-documents/preview", {
          params: { path: { business_id: b.id } },
          body,
        }),
      );
      setPayload(body);
      return impact;
    },
  });
  const post = useMutation({
    mutationFn: (body: components["schemas"]["DocumentCreate"]) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/trade-documents", {
          params: { path: { business_id: b.id } },
          body,
        }),
      ),
    onSuccess: () => {
      setDocumentKind(payload?.kind || kind);
      setOpen(false);
      setPayload(undefined);
      preview.reset();
      post.reset();
      form.reset();
      refresh();
    },
  });
  const saveDraft = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/source-drafts", {
          params: { path: { business_id: b.id } },
          body: payload!,
        }),
      ),
    onSuccess: () => {
      setOpen(false);
      setPayload(undefined);
      refresh();
    },
  });
  const dispute = useMutation({
    mutationFn: (node: HTMLFormElement) => {
      const v = new FormData(node);
      return result(
        api.PATCH(
          "/api/v1/businesses/{business_id}/trade-documents/{document_id}/dispute",
          {
            params: { path: { business_id: b.id, document_id: selected!.id } },
            body: {
              expected_version: selected!.version,
              disputed: !selected!.disputed,
              reason: String(v.get("reason")),
            },
          },
        ),
      );
    },
    onSuccess: (data) => {
      if (selected)
        setSelected({
          ...selected,
          disputed: !selected.disputed,
          version: data.version,
        });
      refresh();
    },
  });
  const pay = useMutation({
    mutationFn: (body: components["schemas"]["PaymentCreate"]) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/payments", {
          params: { path: { business_id: b.id } },
          body,
        }),
      ),
    onSuccess: () => {
      setSelected(null);
      setPaymentBody(undefined);
      refresh();
    },
  });
  const preparingPayment = useRef(false);
  const [preparing, setPreparing] = useState(false);
  const preparePayment = async (node: HTMLFormElement) => {
    if (!selected || preparingPayment.current) return;
    preparingPayment.current = true;
    setPreparing(true);
    const v = new FormData(node);
    try {
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", {
          params: { path: { business_id: b.id } },
        }),
      );
      const body: components["schemas"]["PaymentCreate"] = {
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
        kind: kind === "invoice" ? "receipt" : "payment",
        party_id: selected.party_id,
        accounting_date: String(v.get("date")),
        bank_account_id: String(v.get("bank")),
        control_account_id: selected.control_account_id,
        amount: String(v.get("amount")),
        fee_amount: String(v.get("fee_amount") || "0"),
        fee_account_id: String(v.get("fee_account") || "") || null,
        allocations: [
          { document_id: selected.id, amount: String(v.get("amount")) },
        ],
      };
      setPaymentBody(body);
      pay.reset();
    } catch (error) {
      setPreparationError(error);
    } finally {
      preparingPayment.current = false;
      setPreparing(false);
    }
  };
  const [preparationError, setPreparationError] = useState<unknown>();
  const accountOptions = (control?: string) =>
    accounts.data?.items
      .filter((a) => a.active && (control ? a.control === control : !a.control))
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
        eyebrow={
          kind === "invoice" ? "SALES & RECEIVABLES" : "PURCHASES & PAYABLES"
        }
        title={kind === "invoice" ? "Sales invoices" : "Supplier bills"}
        description={
          kind === "invoice"
            ? "Create invoices, record receipts and keep track of what customers owe."
            : "Record supplier bills and track payments without losing the paper trail."
        }
        actions={
          <Button
            onClick={() => {
              setOpen(true);
              setPayload(undefined);
              preview.reset();
              post.reset();
            }}
          >
            <Plus size={16} />
            New {kind}
          </Button>
        }
      />
      <div className="filterbar">
        <Field label="Document type filter">
          <select
            value={documentKind}
            onChange={(e) => {
              setDocumentKind(e.target.value as typeof documentKind);
              setCursor(undefined);
            }}
          >
            <option value={kind}>
              {kind === "invoice" ? "Invoices" : "Bills"}
            </option>
            {kind === "invoice" ? (
              <>
                <option value="sales_receipt">Sales receipts</option>
                <option value="customer_credit">Customer credit notes</option>
              </>
            ) : (
              <option value="vendor_credit">Supplier credit notes</option>
            )}
          </select>
        </Field>
        <Field label="Balances as of">
          <input
            type="date"
            value={asOf}
            onChange={(e) => {
              setAsOf(e.target.value);
              setCursor(undefined);
            }}
          />
        </Field>
        <Badge>
          {b.currency} · {b.basis}
        </Badge>
      </div>
      <ErrorNotice error={docs.error || accounts.error || parties.error} />
      <section className="panel">
        {docs.isPending ? (
          <Loading />
        ) : docs.data?.items.length ? (
          <>
            <Table
              caption={
                kind === "invoice" ? "Customer invoices" : "Supplier bills"
              }
              rows={docs.data.items}
              rowKey={(d) => d.id}
              columns={[
                { label: "Number", render: (d) => <strong>{d.number}</strong> },
                { label: "Issued", render: (d) => d.accounting_date },
                { label: "Due", render: (d) => d.due_date },
                { label: "Total", render: (d) => money(d.total, b.currency) },
                {
                  label: "Outstanding",
                  render: (d) =>
                    d.outstanding === null
                      ? "Unavailable"
                      : money(d.outstanding, b.currency),
                },
                {
                  label: "Status",
                  render: (d) => (
                    <Badge tone={d.disputed ? "warning" : "neutral"}>
                      {d.disputed
                        ? "Disputed"
                        : d.outstanding && /^0(?:\.0+)?$/.test(d.outstanding)
                          ? "Paid"
                          : "Open"}
                    </Badge>
                  ),
                },
                {
                  label: "Action",
                  render: (d) => (
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setSelected(d);
                        setPaymentBody(undefined);
                        setPreparationError(undefined);
                        pay.reset();
                      }}
                    >
                      View & record <ArrowUpRight size={14} />
                    </Button>
                  ),
                },
              ]}
            />
            <NextPage
              cursor={docs.data.next_cursor}
              onNext={() => setCursor(docs.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title={`Your first ${kind} starts here.`}>
            Add a contact and the relevant accounts, then create a reviewed
            entry.
          </Empty>
        )}
      </section>
      <Modal
        open={open}
        onClose={() => {
          if (!post.isPending) setOpen(false);
        }}
        title={payload ? `Review ${kind}` : `Create ${kind}`}
        description="An authorized accountant must approve the posting. Financial totals are calculated by the server."
      >
        <ErrorNotice error={preview.error || post.error || saveDraft.error} />
        {payload ? (
          <>
            <dl className="review-summary">
              <dt>Document</dt>
              <dd>{payload.number}</dd>
              <dt>Date</dt>
              <dd>{payload.accounting_date}</dd>
              <dt>Due</dt>
              <dd>{payload.due_date}</dd>
            </dl>
            {preview.data && (
              <dl className="review-summary">
                <dt>Server total</dt>
                <dd>{money(preview.data.total, b.currency)}</dd>
                <dt>Tax</dt>
                <dd>{money(preview.data.tax, b.currency)}</dd>
              </dl>
            )}
            <Table
              caption="Reviewed document lines"
              rows={payload.lines}
              rowKey={(line) => `${line.account_id}-${line.description}`}
              columns={[
                { label: "Description", render: (l) => l.description },
                { label: "Amount", render: (l) => money(l.amount, b.currency) },
              ]}
            />
            <div className="notice">
              Posting creates an immutable financial entry. Corrections require
              a linked adjustment.
            </div>
            <div className="actions">
              <Button
                variant="secondary"
                disabled={post.isPending}
                onClick={() => setPayload(undefined)}
              >
                Back to edit
              </Button>
              <Button
                variant="secondary"
                busy={saveDraft.isPending}
                disabled={post.isPending}
                onClick={() => saveDraft.mutate()}
              >
                Save as draft
              </Button>
              <Button
                permission="post"
                busy={post.isPending || saveDraft.isPending}
                onClick={() => post.mutate(payload)}
              >
                {post.isError ? "Retry same request" : "Approve & post"}
              </Button>
            </div>
          </>
        ) : (
          <form onSubmit={form.handleSubmit((v) => preview.mutate(v))}>
            <div className="form-grid">
              <Field label="Document type">
                <select {...form.register("subtype")}>
                  <option value={kind}>
                    {kind === "invoice" ? "Invoice" : "Bill"}
                  </option>
                  {kind === "invoice" ? (
                    <>
                      <option value="sales_receipt">
                        Immediate sales receipt
                      </option>
                      <option value="customer_credit">
                        Customer credit note
                      </option>
                    </>
                  ) : (
                    <option value="vendor_credit">Supplier credit note</option>
                  )}
                </select>
              </Field>
              <Field label="Bank account (sales receipt)">
                <select {...form.register("bank_account_id")}>
                  <option value="">Not applicable</option>
                  {accounts.data?.items
                    .filter((a) => a.active && a.kind === "asset" && !a.control)
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Original document (credit note)">
                <select {...form.register("original_id")}>
                  <option value="">Not applicable</option>
                  {docs.data?.items.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.number}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Document number">
                <input required {...form.register("number")} />
              </Field>
              <Field label={kind === "invoice" ? "Customer" : "Supplier"}>
                <select required {...form.register("party_id")}>
                  <option value="">Choose a contact</option>
                  {parties.data?.items
                    .filter((p) =>
                      p.roles.includes(
                        kind === "invoice" ? "customer" : "supplier",
                      ),
                    )
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Accounting date">
                <input
                  type="date"
                  required
                  {...form.register("accounting_date")}
                />
              </Field>
              <Field label="Due date">
                <input type="date" required {...form.register("due_date")} />
              </Field>
            </div>
            <Field
              label={
                kind === "invoice"
                  ? "Receivables control account"
                  : "Payables control account"
              }
            >
              <select required {...form.register("control_account_id")}>
                <option value="">Choose control account</option>
                {accountOptions(kind === "invoice" ? "ar" : "ap")}
              </select>
            </Field>
            <fieldset>
              <legend>Line items</legend>
              {lines.fields.map((line, i) => (
                <div className="line-item" key={line.id}>
                  <Field label="Description">
                    <input
                      required
                      {...form.register(`lines.${i}.description`)}
                    />
                  </Field>
                  <Field label="Account">
                    <select
                      required
                      {...form.register(`lines.${i}.account_id`)}
                    >
                      <option value="">Choose account</option>
                      {accountOptions()}
                    </select>
                  </Field>
                  <Field label="Amount">
                    <input
                      required
                      inputMode="decimal"
                      pattern="[0-9]+(\.[0-9]{1,6})?"
                      {...form.register(`lines.${i}.amount`)}
                    />
                  </Field>
                  <Field label="Tax amount">
                    <input
                      inputMode="decimal"
                      required
                      {...form.register(`lines.${i}.tax_amount`)}
                    />
                  </Field>
                  <Field label="Tax control account">
                    <select {...form.register(`lines.${i}.tax_account_id`)}>
                      <option value="">No tax</option>
                      {accountOptions("tax")}
                    </select>
                  </Field>
                  <Field label="Tax code">
                    <input {...form.register(`lines.${i}.tax_code`)} />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    aria-label={`Remove line ${i + 1}`}
                    disabled={lines.fields.length === 1}
                    onClick={() => lines.remove(i)}
                  >
                    <Trash2 size={16} />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  lines.append({
                    tax_amount: "0",
                    tax_account_id: "",
                    tax_code: "",
                    account_id: "",
                    description: "",
                    amount: "",
                  })
                }
              >
                <Plus size={14} />
                Add line
              </Button>
            </fieldset>
            <Button busy={preview.isPending}>Review before posting</Button>
          </form>
        )}
      </Modal>
      <Modal
        open={!!selected}
        onClose={() => {
          if (!pay.isPending) setSelected(null);
        }}
        title={selected?.number || "Document"}
        description="Record a payment that has already happened. This does not move money."
      >
        {selected && (
          <>
            <dl className="review-summary">
              <dt>Total</dt>
              <dd>{money(selected.total, b.currency)}</dd>
              <dt>Outstanding</dt>
              <dd>
                {selected.outstanding === null
                  ? "Unavailable"
                  : money(selected.outstanding, b.currency)}
              </dd>
              <dt>Due</dt>
              <dd>{selected.due_date}</dd>
            </dl>
            <details>
              <summary>
                {selected.disputed
                  ? "Resolve document dispute"
                  : "Flag a document dispute"}
              </summary>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  dispute.mutate(e.currentTarget);
                }}
              >
                <ErrorNotice error={dispute.error} />
                <Field label="Dispute reason">
                  <textarea name="reason" required maxLength={1000} />
                </Field>
                <Button busy={dispute.isPending}>
                  {selected.disputed ? "Resolve dispute" : "Flag dispute"}
                </Button>
                {dispute.isSuccess && (
                  <p role="status">Dispute state updated.</p>
                )}
              </form>
            </details>
            <ErrorNotice error={pay.error || preparationError} />
            {selected.outstanding &&
              !selected.outstanding.startsWith("-") &&
              !/^0(?:\.0+)?$/.test(selected.outstanding) &&
              (paymentBody ? (
                <>
                  <dl className="review-summary">
                    <dt>Payment date</dt>
                    <dd>{paymentBody.accounting_date}</dd>
                    <dt>Amount</dt>
                    <dd>{money(paymentBody.amount, b.currency)}</dd>
                    <dt>Fee</dt>
                    <dd>{money(paymentBody.fee_amount, b.currency)}</dd>
                    <dt>Bank account</dt>
                    <dd>
                      {
                        accounts.data?.items.find(
                          (a) => a.id === paymentBody.bank_account_id,
                        )?.name
                      }
                    </dd>
                  </dl>
                  <Button
                    variant="secondary"
                    disabled={pay.isPending}
                    onClick={() => setPaymentBody(undefined)}
                  >
                    Back to edit
                  </Button>
                  <Button
                    permission="post"
                    busy={pay.isPending || preparing}
                    onClick={() => pay.mutate(paymentBody)}
                  >
                    {pay.isError
                      ? "Retry same payment request"
                      : "Approve payment record"}
                  </Button>
                </>
              ) : (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void preparePayment(e.currentTarget);
                  }}
                >
                  <Field label="Bank account">
                    <select name="bank" required>
                      <option value="">Choose account</option>
                      {accounts.data?.items
                        .filter((a) => a.kind === "asset" && a.active)
                        .map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <div className="form-grid">
                    <Field label="Payment date">
                      <input
                        name="date"
                        type="date"
                        required
                        defaultValue={today(b.timezone)}
                      />
                    </Field>
                    <Field label="Amount received / paid">
                      <input
                        name="amount"
                        required
                        inputMode="decimal"
                        pattern="[0-9]+(\.[0-9]{1,6})?"
                        defaultValue={selected.outstanding || ""}
                      />
                    </Field>
                  </div>
                  <Field
                    label="Settlement fee"
                    hint="Enter a recorded fee separately from the amount allocated to this document."
                  >
                    <input
                      name="fee_amount"
                      inputMode="decimal"
                      required
                      defaultValue="0"
                      pattern="[0-9]+(\.[0-9]{1,6})?"
                    />
                  </Field>
                  <Field label="Fee expense account">
                    <select name="fee_account">
                      <option value="">No fee</option>
                      {accounts.data?.items
                        .filter(
                          (a) => a.active && !a.control && a.kind === "expense",
                        )
                        .map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <Button permission="post" busy={pay.isPending || preparing}>
                    Review payment
                  </Button>
                </form>
              ))}
          </>
        )}
      </Modal>
    </>
  );
}
