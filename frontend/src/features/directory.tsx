import { BeneficiaryReview } from "./beneficiary-review";
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
  money,
  NextPage,
  PageHeading,
  Table,
} from "../components/ui";
import { useBusiness, today } from "./context";
import type { components } from "../api/generated/schema";

export function Accounts() {
  const b = useBusiness();
  const [cursor, setCursor] = useState<string | undefined>();
  const [open, setOpen] = useState(false);
  const accounts = useQuery({
    queryKey: [...keys.business(b.id), "accounts", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path: { business_id: b.id }, query: { limit: 50, cursor } },
        }),
      ),
  });
  const create = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      const body: components["schemas"]["AccountCreate"] = {
        name: String(v.get("name")),
        code: String(v.get("code")),
        kind: v.get("kind") as components["schemas"]["AccountCreate"]["kind"],
        control: (v.get("control") ||
          null) as components["schemas"]["AccountCreate"]["control"],
      };
      return result(
        api.POST("/api/v1/businesses/{business_id}/accounts", {
          params: { path: { business_id: b.id } },
          body,
        }),
      );
    },
    onSuccess: () => {
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <>
      <PageHeading
        title="A place for every transaction."
        eyebrow="CHART OF ACCOUNTS"
        description="Give your books a structure that makes sense for your business."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus size={16} />
            New account
          </Button>
        }
      />
      <ErrorNotice
        error={accounts.error}
        retry={() => {
          void accounts.refetch();
        }}
      />
      <section className="panel">
        {accounts.isPending ? (
          <Loading />
        ) : accounts.data?.items.length ? (
          <>
            <Table
              caption="Chart of accounts"
              rows={accounts.data.items}
              rowKey={(a) => a.id}
              columns={[
                {
                  label: "Code",
                  render: (a) => <span className="mono">{a.code}</span>,
                },
                { label: "Account", render: (a) => <strong>{a.name}</strong> },
                { label: "Type", render: (a) => <Badge>{a.kind}</Badge> },
                { label: "Control", render: (a) => a.control || "—" },
                {
                  label: "Status",
                  render: (a) => (
                    <Badge tone={a.active ? "good" : "neutral"}>
                      {a.active ? "Active" : "Inactive"}
                    </Badge>
                  ),
                },
              ]}
            />
            <NextPage
              cursor={accounts.data.next_cursor}
              onNext={() => setCursor(accounts.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="Set the foundation for your books.">
            Add your bank, income, expense and control accounts.
          </Empty>
        )}
      </section>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="New account"
        description="Choose its accounting classification carefully; posted history stays attached to this account."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={create.error} />
          <div className="form-grid">
            <Field label="Account code">
              <input
                name="code"
                required
                maxLength={30}
                pattern="[A-Za-z0-9_.-]+"
              />
            </Field>
            <Field label="Account name">
              <input name="name" required maxLength={120} />
            </Field>
          </div>
          <Field label="Classification">
            <select name="kind">
              {["asset", "liability", "equity", "income", "expense"].map(
                (k) => (
                  <option key={k}>{k}</option>
                ),
              )}
            </select>
          </Field>
          <Field label="Control account purpose">
            <select name="control">
              <option value="">Ordinary account</option>
              {[
                "ar",
                "ap",
                "loan_receivable",
                "loan_payable",
                "tax",
                "clearing",
              ].map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </Field>
          <Button busy={create.isPending}>Create account</Button>
        </form>
      </Modal>
    </>
  );
}

export function People() {
  const b = useBusiness();
  const [cursor, setCursor] = useState<string | undefined>();
  const [party, setParty] = useState<components["schemas"]["PartyRead"]>();
  const [role, setRole] = useState<
    "customer" | "supplier" | "lender" | "borrower"
  >("customer");
  const [statementCursor, setStatementCursor] = useState<string>();
  const statement = useQuery({
    queryKey: [
      ...keys.business(b.id),
      "party-statement",
      party?.id,
      role,
      statementCursor,
    ],
    enabled: !!party,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/parties/{party_id}/statement",
          {
            signal,
            params: {
              path: { business_id: b.id, party_id: party!.id },
              query: {
                role,
                as_of: today(b.timezone),
                cursor: statementCursor,
                limit: 50,
              },
            },
          },
        ),
      ),
  });
  const [open, setOpen] = useState(false);
  const people = useQuery({
    queryKey: [...keys.business(b.id), "parties", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/parties", {
          signal,
          params: { path: { business_id: b.id }, query: { limit: 50, cursor } },
        }),
      ),
  });
  const create = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/parties", {
          params: { path: { business_id: b.id } },
          body: {
            name: String(v.get("name")),
            email: String(v.get("email")) || null,
            roles: v.getAll(
              "roles",
            ) as components["schemas"]["PartyCreate"]["roles"],
          },
        }),
      );
    },
    onSuccess: () => {
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="PEOPLE & BUSINESSES"
        title="Good relationships. Clear records."
        description="Keep your customers, suppliers, lenders and borrowers in one place."
        actions={
          <Button onClick={() => setOpen(true)}>
            <Plus size={16} />
            Add contact
          </Button>
        }
      />
      <ErrorNotice error={people.error} />
      <section className="panel">
        {people.isPending ? (
          <Loading />
        ) : people.data?.items.length ? (
          <>
            <Table
              caption="Business contacts"
              rows={people.data.items}
              rowKey={(p) => p.id}
              columns={[
                {
                  label: "Name",
                  render: (p) => (
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setParty(p);
                        setRole(
                          (p.roles.find((r) =>
                            [
                              "customer",
                              "supplier",
                              "lender",
                              "borrower",
                            ].includes(r),
                          ) || "customer") as typeof role,
                        );
                        setStatementCursor(undefined);
                      }}
                    >
                      {p.name}
                    </Button>
                  ),
                },
                { label: "Email", render: (p) => p.email || "—" },
                {
                  label: "Relationship",
                  render: (p) => (
                    <div className="badges">
                      {p.roles.map((r) => (
                        <Badge key={r}>{r}</Badge>
                      ))}
                    </div>
                  ),
                },
                {
                  label: "Status",
                  render: (p) => (p.active ? "Active" : "Inactive"),
                },
              ]}
            />
            <NextPage
              cursor={people.data.next_cursor}
              onNext={() => setCursor(people.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="Your relationships start here.">
            Add a customer or supplier before creating an invoice or bill.
          </Empty>
        )}
      </section>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Add a contact"
        description="One contact can have more than one relationship with your business."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={create.error} />
          <Field label="Name">
            <input name="name" required maxLength={160} />
          </Field>
          <Field label="Email (optional)">
            <input name="email" type="email" />
          </Field>
          <fieldset>
            <legend>Relationship</legend>
            {["customer", "supplier", "lender", "borrower"].map((r, i) => (
              <label className="check" key={r}>
                <input
                  type="checkbox"
                  name="roles"
                  value={r}
                  defaultChecked={i === 0}
                />
                {r}
              </label>
            ))}
          </fieldset>
          <Button busy={create.isPending}>Add contact</Button>
        </form>
      </Modal>
      <Modal
        open={!!party}
        onClose={() => setParty(undefined)}
        title={party?.name || "Contact statement"}
        description="Complete server-calculated balances, with dated source entries."
      >
        {party?.roles.includes("supplier") && (
          <BeneficiaryReview key={party.id} party={party} />
        )}
        <Field label="Relationship">
          <select
            value={role}
            onChange={(e) => {
              setRole(e.target.value as typeof role);
              setStatementCursor(undefined);
            }}
          >
            {party?.roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
        <ErrorNotice error={statement.error} />
        {statement.data && (
          <>
            <dl className="review-summary">
              <dt>Balance as of {statement.data.as_of}</dt>
              <dd>{money(statement.data.total, b.currency)}</dd>
            </dl>
            <p className="muted">{statement.data.balance_definition}</p>
            <Table
              caption="Contact statement history"
              rows={statement.data.items}
              rowKey={(r) => r.id}
              columns={[
                { label: "Date", render: (r) => r.accounting_date },
                { label: "Document", render: (r) => r.number },
                { label: "Type", render: (r) => r.kind },
                { label: "Amount", render: (r) => money(r.amount, b.currency) },
                {
                  label: "Running balance",
                  render: (r) => money(r.running_balance, b.currency),
                },
              ]}
            />
            <NextPage
              cursor={statement.data.next_cursor}
              onNext={() =>
                setStatementCursor(statement.data?.next_cursor || undefined)
              }
              onReset={() => setStatementCursor(undefined)}
            />
          </>
        )}
      </Modal>
    </>
  );
}
