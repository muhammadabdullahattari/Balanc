import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  PageHeading,
  Table,
  money,
} from "../components/ui";
import { today, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Loans() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [schedule, setSchedule] = useState<
    { due_date: string; principal: string; interest: string }[]
  >([]);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<components["schemas"]["LoanRead"]>();
  const [body, setBody] =
    useState<components["schemas"]["LoanMovementCreate"]>();
  const loans = useQuery({
    queryKey: [...keys.business(b.id), "loans"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/loans", {
          signal,
          params: { path, query: { as_of: today(b.timezone), limit: 200 } },
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
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };
  const create = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/loans", {
          params: { path },
          body: {
            schedule,
            name: String(v.get("name")),
            party_id: String(v.get("party")),
            direction:
              v.get("direction") === "receivable" ? "receivable" : "payable",
            control_account_id: String(v.get("control")),
          },
        }),
      );
    },
    onSuccess: () => {
      setOpen(false);
      refresh();
    },
  });
  const movement = useMutation({
    mutationFn: (command: components["schemas"]["LoanMovementCreate"]) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/loans/{loan_id}/movements", {
          params: { path: { ...path, loan_id: selected!.id } },
          body: command,
        }),
      ),
    onSuccess: () => {
      setSelected(undefined);
      setBody(undefined);
      refresh();
    },
  });
  const prepare = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const v = new FormData(form);
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      setBody({
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
        kind: v.get("kind") === "repayment" ? "repayment" : "drawdown",
        accounting_date: String(v.get("date")),
        bank_account_id: String(v.get("bank")),
        principal: String(v.get("principal")),
        fees: String(v.get("fees") || "0"),
        fee_account_id: String(v.get("fee_account") || "") || null,
        interest: String(v.get("interest")) || "0",
        interest_account_id: String(v.get("interest_account")) || null,
      });
    },
  });
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="LOANS & FINANCING"
        title="Keep the principal in perspective."
        description="Track borrowing and lending, with principal and interest recorded separately."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus size={16} />
            Add loan
          </Button>
        }
      />
      <ErrorNotice error={loans.error || accounts.error || parties.error} />
      <section className="panel">
        {loans.isPending ? (
          <Loading />
        ) : loans.data?.items.length ? (
          <Table
            caption="Loans"
            rows={loans.data.items}
            rowKey={(l) => l.id}
            columns={[
              { label: "Loan", render: (l) => <strong>{l.name}</strong> },
              {
                label: "Direction",
                render: (l) => <Badge>{l.direction}</Badge>,
              },
              {
                label: "Principal outstanding",
                render: (l) => money(l.principal, b.currency),
              },
              {
                label: "Action",
                render: (l) => (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setSelected(l);
                      setBody(undefined);
                      movement.reset();
                      prepare.reset();
                    }}
                  >
                    Record movement
                  </Button>
                ),
              },
            ]}
          />
        ) : (
          <Empty title="No loans on your books yet.">
            Add a reviewed loan account before recording principal or
            repayments.
          </Empty>
        )}
      </section>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Add a loan"
        description="Link the counterparty and the appropriate loan control account."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={create.error} />
          <Field label="Loan name">
            <input name="name" required />
          </Field>
          <Field label="Lender / borrower">
            <select name="party" required>
              <option value="">Choose contact</option>
              {parties.data?.items
                .filter(
                  (p) =>
                    p.roles.includes("lender") || p.roles.includes("borrower"),
                )
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Direction">
            <select name="direction">
              <option value="payable">We borrowed money</option>
              <option value="receivable">We lent money</option>
            </select>
          </Field>
          <Field label="Loan control account">
            <select name="control" required>
              <option value="">Choose account</option>
              {accounts.data?.items
                .filter((a) => a.control?.startsWith("loan_"))
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </Field>
          <fieldset>
            <legend>Repayment schedule (optional)</legend>
            <p className="muted">
              Scheduled amounts inform forecasts and do not post repayments.
            </p>
            {schedule.map((row, i) => (
              <div className="form-grid" key={i}>
                <Field label={`Due date ${i + 1}`}>
                  <input
                    type="date"
                    required
                    value={row.due_date}
                    onChange={(e) =>
                      setSchedule((rows) =>
                        rows.map((r, j) =>
                          j === i ? { ...r, due_date: e.target.value } : r,
                        ),
                      )
                    }
                  />
                </Field>
                <Field label={`Scheduled principal ${i + 1}`}>
                  <input
                    inputMode="decimal"
                    required
                    value={row.principal}
                    onChange={(e) =>
                      setSchedule((rows) =>
                        rows.map((r, j) =>
                          j === i ? { ...r, principal: e.target.value } : r,
                        ),
                      )
                    }
                  />
                </Field>
                <Field label={`Scheduled interest ${i + 1}`}>
                  <input
                    inputMode="decimal"
                    required
                    value={row.interest}
                    onChange={(e) =>
                      setSchedule((rows) =>
                        rows.map((r, j) =>
                          j === i ? { ...r, interest: e.target.value } : r,
                        ),
                      )
                    }
                  />
                </Field>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    setSchedule((rows) => rows.filter((_, j) => j !== i))
                  }
                >
                  Remove schedule {i + 1}
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="secondary"
              disabled={schedule.length >= 120}
              onClick={() =>
                setSchedule((rows) => [
                  ...rows,
                  { due_date: today(b.timezone), principal: "", interest: "0" },
                ])
              }
            >
              Add scheduled repayment
            </Button>
          </fieldset>
          <Button busy={create.isPending}>Add loan</Button>
        </form>
      </Modal>
      <Modal
        open={!!selected}
        onClose={() => {
          if (!movement.isPending) setSelected(undefined);
        }}
        title={selected?.name || "Loan movement"}
        description="Record an actual loan movement. This action does not transfer money."
      >
        <ErrorNotice error={movement.error || prepare.error} />
        {body ? (
          <>
            <dl className="review-summary">
              <dt>Movement</dt>
              <dd>{body.kind}</dd>
              <dt>Principal</dt>
              <dd>{money(body.principal, b.currency)}</dd>
              <dt>Interest</dt>
              <dd>{money(body.interest || "0", b.currency)}</dd>
              <dt>Fees</dt>
              <dd>{money(body.fees, b.currency)}</dd>
              <dt>Date</dt>
              <dd>{body.accounting_date}</dd>
            </dl>
            <div className="actions">
              <Button variant="secondary" onClick={() => setBody(undefined)}>
                Back
              </Button>
              <Button
                permission="post"
                busy={movement.isPending}
                onClick={() => movement.mutate(body)}
              >
                {movement.isError ? "Retry same request" : "Confirm movement"}
              </Button>
            </div>
          </>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              prepare.mutate(e.currentTarget);
            }}
          >
            <div className="form-grid">
              <Field label="Movement">
                <select name="kind">
                  <option value="drawdown">Drawdown</option>
                  <option value="repayment">Repayment</option>
                </select>
              </Field>
              <Field label="Accounting date">
                <input
                  name="date"
                  type="date"
                  required
                  defaultValue={today(b.timezone)}
                />
              </Field>
              <Field label="Principal">
                <input
                  name="principal"
                  inputMode="decimal"
                  required
                  pattern="[0-9]+(\.[0-9]{1,6})?"
                />
              </Field>
              <Field label="Interest">
                <input
                  name="interest"
                  inputMode="decimal"
                  defaultValue="0"
                  pattern="[0-9]+(\.[0-9]{1,6})?"
                />
              </Field>
            </div>
            <Field label="Bank account">
              <select name="bank" required>
                <option value="">Choose account</option>
                {accounts.data?.items
                  .filter((a) => a.kind === "asset" && !a.control)
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Interest account">
              <select name="interest_account">
                <option value="">No interest</option>
                {accounts.data?.items
                  .filter((a) => ["income", "expense"].includes(a.kind))
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Loan fees">
              <input
                name="fees"
                required
                inputMode="decimal"
                defaultValue="0"
              />
            </Field>
            <Field label="Fee account">
              <select name="fee_account">
                <option value="">No fees</option>
                {accounts.data?.items
                  .filter((a) => a.active && !a.control && a.kind === "expense")
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
              </select>
            </Field>
            <Button busy={prepare.isPending}>Review movement</Button>
          </form>
        )}
      </Modal>
    </>
  );
}
