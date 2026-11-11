import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import type { components } from "../api/generated/schema";
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
import { today, useAccess, useBusiness } from "./context";

type Line = components["schemas"]["LedgerProjectLine"];
type Action =
  | { kind: "create" }
  | { kind: "tag"; line: Line }
  | { kind: "draft"; line: Line; key: string };

export function Projects() {
  const business = useBusiness();
  const access = useAccess();
  const writer = ["owner", "accountant", "bookkeeper"].some(
    (role) => role === access?.role,
  );
  const day = today(business.timezone);
  const [start, setStart] = useState(day.slice(0, 7) + "-01");
  const [end, setEnd] = useState(day);
  const [project, setProject] = useState("");
  const [action, setAction] = useState<Action>();
  const [draftSaved, setDraftSaved] = useState(false);
  const path = { business_id: business.id };
  const prefix = [...keys.business(business.id), "projects"];
  const available = business.p2_projects_available === true;
  const projects = useQuery({
    queryKey: prefix,
    enabled: available,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/projects", {
          signal,
          params: { path },
        }),
      ),
  });
  const lines = useQuery({
    queryKey: [...prefix, "lines", start, end],
    enabled: available && !!start && !!end && start <= end,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/projects/ledger-lines", {
          signal,
          params: { path, query: { start, end } },
        }),
      ),
  });
  const totals = useQuery({
    queryKey: [...prefix, "totals", project, start, end],
    enabled: available && !!project && !!start && !!end && start <= end,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/projects/{project_id}/totals",
          {
            signal,
            params: {
              path: { ...path, project_id: project },
              query: { start, end },
            },
          },
        ),
      ),
  });
  const parties = useQuery({
    queryKey: [...prefix, "customers"],
    enabled: action?.kind === "create",
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/parties", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const accounts = useQuery({
    queryKey: [...prefix, "accounts"],
    enabled: action?.kind === "draft",
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const save = useMutation({
    mutationFn: async ({
      selected,
      form,
    }: {
      selected: Action;
      form: FormData;
    }) => {
      const value = (key: string) => String(form.get(key) ?? "").trim();
      if (selected.kind === "create") {
        const created = await result(
          api.POST("/api/v1/businesses/{business_id}/projects", {
            params: { path },
            body: { name: value("name"), customer_id: value("customer") },
          }),
        );
        setProject(created.id);
      } else if (selected.kind === "tag") {
        await result(
          api.POST(
            "/api/v1/businesses/{business_id}/projects/ledger-lines/{line_id}/tag",
            {
              params: { path: { ...path, line_id: selected.line.id } },
              body: {
                project_id: value("project"),
                location: value("location") || null,
                billable: form.has("billable"),
              },
            },
          ),
        );
      } else {
        await result(
          api.POST(
            "/api/v1/businesses/{business_id}/projects/billable-drafts",
            {
              params: { path },
              body: {
                source_line_id: selected.line.id,
                idempotency_key: selected.key,
                number: value("number"),
                description: value("description"),
                accounting_date: value("date"),
                due_date: value("due"),
                income_account_id: value("income"),
                control_account_id: value("receivable"),
                tax_amount: value("tax_amount") || "0",
                tax_account_id: value("tax_account") || null,
                tax_code: value("tax_code") || null,
              },
            },
          ),
        );
        setDraftSaved(true);
      }
    },
    onSuccess: () => {
      setAction(undefined);
      void queryClient.invalidateQueries({
        queryKey: keys.business(business.id),
      });
    },
  });
  const open = (next: Action) => {
    save.reset();
    setDraftSaved(false);
    setAction(next);
  };
  if (!available)
    return <Empty title="Projects are unavailable in this workspace." />;
  return (
    <>
      <PageHeading
        title="Projects and billable expenses"
        description="Attribute posted ledger lines to a customer project and review costs before invoicing."
        actions={
          writer && (
            <Button onClick={() => open({ kind: "create" })}>
              New project
            </Button>
          )
        }
      />
      <ErrorNotice error={projects.error || lines.error || totals.error} />
      <div className="filterbar">
        <Field label="Project">
          <select
            value={project}
            onChange={(event) => setProject(event.target.value)}
          >
            <option value="">All lines</option>
            {projects.data?.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="From">
          <input
            type="date"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            min={start}
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
        </Field>
      </div>
      {totals.data && (
        <section
          className="metric-grid"
          aria-label="Project posted ledger totals"
        >
          {(["income", "cost", "margin"] as const).map((key) => (
            <article className="metric" key={key}>
              <div>{key}</div>
              <strong>{money(totals.data[key], business.currency)}</strong>
            </article>
          ))}
        </section>
      )}
      {draftSaved && (
        <p role="status" className="notice">
          The cost is reserved. <Link to="/drafts">Open Draft review</Link> to
          issue or void the invoice.
        </p>
      )}
      <section className="panel">
        {lines.isFetching && !lines.data ? (
          <Loading />
        ) : lines.data?.items.length ? (
          <Table
            caption="Posted income and expense lines"
            rows={lines.data.items.filter(
              (line) =>
                !project || line.project_id === project || !line.project_id,
            )}
            rowKey={(line) => line.id}
            columns={[
              { label: "Date", render: (line) => line.accounting_date },
              {
                label: "Source",
                render: (line) => (
                  <>
                    {line.memo}
                    <br />
                    <small>{line.account_name}</small>
                  </>
                ),
              },
              {
                label: "Debit / credit",
                render: (line) => (
                  <>
                    {money(line.debit, business.currency)} /{" "}
                    {money(line.credit, business.currency)}
                  </>
                ),
              },
              {
                label: "Project",
                render: (line) =>
                  projects.data?.items.find(
                    (item) => item.id === line.project_id,
                  )?.name || "Untagged",
              },
              { label: "Location", render: (line) => line.location || "—" },
              {
                label: "Billing",
                render: (line) =>
                  line.billing_state ? (
                    <Badge>{line.billing_state}</Badge>
                  ) : line.billable ? (
                    <Badge>Unbilled</Badge>
                  ) : (
                    "—"
                  ),
              },
              {
                label: "Action",
                render: (line) =>
                  writer &&
                  (!line.project_id ? (
                    <Button
                      variant="secondary"
                      disabled={!projects.data?.items.length}
                      onClick={() => open({ kind: "tag", line })}
                    >
                      Tag line
                    </Button>
                  ) : line.billable && line.eligible && !line.billing_state ? (
                    <Button
                      variant="secondary"
                      onClick={() =>
                        open({ kind: "draft", line, key: crypto.randomUUID() })
                      }
                    >
                      Draft invoice
                    </Button>
                  ) : null),
              },
            ]}
          />
        ) : (
          <Empty title="No posted lines in this date range." />
        )}
      </section>
      <Modal
        open={!!action}
        onClose={() => {
          if (!save.isPending) setAction(undefined);
        }}
        title={
          action?.kind === "create"
            ? "New project"
            : action?.kind === "tag"
              ? "Tag posted line"
              : "Draft a billable invoice"
        }
        description="Posted ledger amounts stay intact. An authorized approver issues the invoice from Draft review."
      >
        <ErrorNotice error={save.error || parties.error || accounts.error} />
        {action && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate({
                selected: action,
                form: new FormData(event.currentTarget),
              });
            }}
          >
            {action.kind === "create" ? (
              <>
                <Field label="Project name">
                  <input name="name" required maxLength={120} />
                </Field>
                <Field label="Customer">
                  <select name="customer" required defaultValue="">
                    <option value="">Choose a customer</option>
                    {parties.data?.items
                      .filter(
                        (item) =>
                          item.active && item.roles.includes("customer"),
                      )
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                  </select>
                </Field>
              </>
            ) : action.kind === "tag" ? (
              <>
                <Field label="Project">
                  <select name="project" required defaultValue={project}>
                    <option value="">Choose a project</option>
                    {projects.data?.items.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Location">
                  <input name="location" maxLength={120} />
                </Field>
                {action.line.eligible && (
                  <label className="check">
                    <input type="checkbox" name="billable" /> Bill this expense
                    to the project customer
                  </label>
                )}
              </>
            ) : (
              <>
                <p>
                  Source cost: {money(action.line.debit, business.currency)}
                </p>
                <div className="form-grid">
                  <Field label="Invoice number">
                    <input name="number" required maxLength={80} />
                  </Field>
                  <Field label="Description">
                    <input name="description" required maxLength={300} />
                  </Field>
                  <Field label="Invoice date">
                    <input
                      name="date"
                      type="date"
                      required
                      min={action.line.accounting_date}
                      defaultValue={day}
                    />
                  </Field>
                  <Field label="Due date">
                    <input name="due" type="date" required defaultValue={day} />
                  </Field>
                  {(
                    [
                      ["Receivables account", "receivable"],
                      ["Income account", "income"],
                      ["Tax liability account", "tax_account"],
                    ] as const
                  ).map(([label, name]) => (
                    <Field label={label} key={name}>
                      <select
                        name={name}
                        required={name !== "tax_account"}
                        defaultValue=""
                      >
                        <option value="">Select an account</option>
                        {accounts.data?.items
                          .filter(
                            (item) =>
                              item.active &&
                              (name === "receivable"
                                ? item.control === "ar"
                                : name === "income"
                                  ? item.kind === "income" && !item.control
                                  : item.kind === "liability" &&
                                    item.control === "tax"),
                          )
                          .map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.name}
                            </option>
                          ))}
                      </select>
                    </Field>
                  ))}
                  <Field label="Tax amount">
                    <input
                      name="tax_amount"
                      inputMode="decimal"
                      defaultValue="0"
                    />
                  </Field>
                  <Field label="Tax code">
                    <input name="tax_code" maxLength={40} />
                  </Field>
                </div>
              </>
            )}
            <Button type="submit" busy={save.isPending}>
              {action.kind === "draft" ? "Save invoice draft" : "Save"}
            </Button>
          </form>
        )}
      </Modal>
    </>
  );
}
