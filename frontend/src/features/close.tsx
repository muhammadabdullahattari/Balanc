import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Plus } from "lucide-react";
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
  PageHeading,
  Table,
} from "../components/ui";
import { today, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Close() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [lockTarget, setLockTarget] =
    useState<components["schemas"]["PeriodRead"]>();
  const [period, setPeriod] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] =
    useState<components["schemas"]["CloseTaskRead"]>();
  const periods = useQuery({
    queryKey: [...keys.business(b.id), "periods"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/periods", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const tasks = useQuery({
    queryKey: [...keys.business(b.id), "tasks", period],
    enabled: !!period,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/close-tasks", {
          signal,
          params: { path, query: { period_id: period, limit: 200 } },
        }),
      ),
  });
  const readinessDate =
    periods.data?.items.find((p) => p.id === period)?.end_date ||
    today(b.timezone);
  const readiness = useQuery({
    queryKey: [...keys.business(b.id), "readiness", readinessDate],
    enabled: !!periods.data,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/readiness", {
          signal,
          params: { path, query: { as_of: readinessDate } },
        }),
      ),
  });
  const members = useQuery({
    queryKey: [...keys.business(b.id), "members"],
    enabled: open,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/memberships", {
          signal,
          params: { path },
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
        api.POST("/api/v1/businesses/{business_id}/close-tasks", {
          params: { path },
          body: {
            period_id: period,
            name: String(v.get("name")),
            assigned_to: String(v.get("assigned")),
            required: v.get("required") === "on",
          },
        }),
      );
    },
    onSuccess: () => {
      setOpen(false);
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.PATCH("/api/v1/businesses/{business_id}/close-tasks/{task_id}", {
          params: { path: { ...path, task_id: selected!.id } },
          body: {
            expected_version: selected!.version,
            state: v.get("state") as "open" | "prepared" | "approved",
            ...(v.get("state") === "approved"
              ? { review_comment: String(v.get("review_comment") || "") }
              : {
                  comment: String(v.get("comment")),
                  evidence_id: String(v.get("evidence")) || null,
                }),
          },
        }),
      );
    },
    onSuccess: () => {
      setSelected(undefined);
      refresh();
    },
  });
  const newPeriod = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/periods", {
          params: { path },
          body: {
            start_date: String(v.get("start")),
            end_date: String(v.get("end")),
          },
        }),
      );
    },
    onSuccess: () => refresh(),
  });
  const lock = useMutation({
    mutationFn: (item: components["schemas"]["PeriodRead"]) =>
      result(
        api.PATCH("/api/v1/businesses/{business_id}/periods/{period_id}", {
          params: { path: { ...path, period_id: item.id } },
          body: { locked: !item.locked, expected_version: item.version },
        }),
      ),
    onSuccess: () => {
      setLockTarget(undefined);
      refresh();
    },
  });
  const active = periods.data?.items.find((p) => p.id === period);
  return (
    <>
      <PageHeading
        eyebrow="MONTH-END CLOSE"
        title="Month-end close"
        description="Prepare, review and lock your books with a clear record of who approved what."
        actions={
          <Button disabled={!period} onClick={() => setOpen(true)}>
            <Plus size={16} />
            Add close task
          </Button>
        }
      />
      <ErrorNotice
        error={periods.error || tasks.error || readiness.error || lock.error}
      />
      <div className="filterbar">
        <Field label="Accounting period">
          <select value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="">Choose a period</option>
            {periods.data?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.start_date} — {p.end_date}
                {p.locked ? " · Locked" : ""}
              </option>
            ))}
          </select>
        </Field>
        {active && (
          <Button
            variant="secondary"
            busy={lock.isPending}
            onClick={() => setLockTarget(active)}
          >
            {active.locked ? "Reopen period" : "Lock period"}
          </Button>
        )}
      </div>
      {readiness.data && (
        <section className="readiness-banner">
          <CheckCircle2 />
          <div>
            <h2>
              {readiness.data.ready
                ? "Your checks are looking good."
                : "A few things need your attention."}
            </h2>
            <p>
              {readiness.data.unreviewed_bank_rows} bank rows to review ·{" "}
              {readiness.data.mandatory_tasks_pending} required tasks pending
              {!!readiness.data.prepaid_installments_pending &&
                ` · ${readiness.data.prepaid_installments_pending} prepaid installments pending`}
            </p>
          </div>
          <Badge tone={readiness.data.ready ? "good" : "warning"}>
            {readiness.data.ready ? "Ready" : "In progress"}
          </Badge>
        </section>
      )}
      {b.p2_micro_close_available && <NightlyWorkpaper key={b.id} />}
      <section className="panel">
        {!period ? (
          <Empty title="Choose the period you’re closing.">
            Tasks and approvals are tied to a specific accounting period.
          </Empty>
        ) : tasks.isPending ? (
          <Loading />
        ) : tasks.data?.items.length ? (
          <Table
            caption="Close checklist"
            rows={tasks.data.items}
            rowKey={(t) => t.id}
            columns={[
              { label: "Task", render: (t) => <strong>{t.name}</strong> },
              {
                label: "Required",
                render: (t) => (t.required ? "Yes" : "Optional"),
              },
              {
                label: "Status",
                render: (t) => (
                  <Badge tone={t.state === "approved" ? "good" : "neutral"}>
                    {t.state}
                  </Badge>
                ),
              },
              {
                label: "Action",
                render: (t) => (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setSelected(t);
                      update.reset();
                    }}
                  >
                    Review task
                  </Button>
                ),
              },
            ]}
          />
        ) : (
          <Empty title="Build your close checklist.">
            Assign the work, attach evidence and have an independent reviewer
            approve it.
          </Empty>
        )}
      </section>
      <details className="panel settings-section">
        <summary>Create an accounting period</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            newPeriod.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={newPeriod.error} />
          <div className="form-grid">
            <Field label="Start date">
              <input name="start" type="date" required />
            </Field>
            <Field label="End date">
              <input name="end" type="date" required />
            </Field>
          </div>
          <Button busy={newPeriod.isPending}>Create period</Button>
          {newPeriod.isSuccess && <p role="status">Period created.</p>}
        </form>
      </details>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Add a close task"
        description="Assign a preparer. A different authorized person must approve the completed task."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={create.error || members.error} />
          <Field label="Task name">
            <input name="name" required maxLength={160} />
          </Field>
          <Field label="Assign to member">
            <select name="assigned" required>
              <option value="">Choose member</option>
              {members.data?.items
                .filter((m) => m.active)
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.role} · {m.user_id}
                  </option>
                ))}
            </select>
          </Field>
          <label className="check">
            <input type="checkbox" name="required" defaultChecked />
            Required before close
          </label>
          <Button busy={create.isPending}>Create task</Button>
        </form>
      </Modal>
      <Modal
        open={!!selected}
        onClose={() => setSelected(undefined)}
        title={selected?.name || "Review task"}
        description="Preparation and approval are separate responsibilities."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            update.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={update.error} />
          <Field label="Status">
            <select name="state" defaultValue={selected?.state}>
              <option value="open">Open</option>
              <option value="prepared">Prepared for review</option>
              <option value="approved">Approved</option>
            </select>
          </Field>
          <Field label="Supporting document reference (optional)">
            <input name="evidence" defaultValue={selected?.evidence_id || ""} />
          </Field>
          <Field label="Review comment">
            <textarea
              name="review_comment"
              maxLength={2000}
              defaultValue={selected?.review_comment}
            />
          </Field>
          <Field label="Preparation comment">
            <textarea
              name="comment"
              maxLength={2000}
              defaultValue={selected?.comment}
            />
          </Field>
          <Button busy={update.isPending}>Save review</Button>
        </form>
      </Modal>
      <Modal
        open={!!lockTarget}
        onClose={() => {
          if (!lock.isPending) setLockTarget(undefined);
        }}
        title={
          lockTarget?.locked
            ? "Reopen accounting period"
            : "Lock accounting period"
        }
        description="Period controls affect financial posting. Resolve outstanding work before locking."
      >
        <ErrorNotice error={lock.error} />
        <p>
          {lockTarget?.start_date} to {lockTarget?.end_date}
        </p>
        <Button
          permission="post"
          busy={lock.isPending}
          onClick={() => {
            if (lockTarget) lock.mutate(lockTarget);
          }}
        >
          {lockTarget?.locked ? "Confirm reopen" : "Confirm period lock"}
        </Button>
      </Modal>
    </>
  );
}

function NightlyWorkpaper() {
  const business = useBusiness();
  const businessToday = today(business.timezone);
  const yesterday = new Date(Date.parse(businessToday) - 86_400_000)
    .toISOString()
    .slice(0, 10);
  const [asOf, setAsOf] = useState(yesterday);
  const query = useQuery({
    queryKey: [...keys.business(business.id), "micro-close", asOf],
    enabled: Boolean(asOf) && asOf <= yesterday,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/micro-close", {
          signal,
          params: {
            path: { business_id: business.id },
            query: { as_of: asOf },
          },
        }),
      ),
  });
  const workpaper = query.data?.workpaper;
  const report = workpaper?.report;
  return (
    <section className="panel settings-section">
      <h2>Nightly close workpaper</h2>
      <Field label="Workpaper date">
        <input
          type="date"
          value={asOf}
          max={yesterday}
          onChange={(event) => setAsOf(event.target.value)}
        />
      </Field>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isFetching && <Loading />}
      {query.isSuccess && !workpaper && (
        <p className="muted">No workpaper was captured for this date.</p>
      )}
      {workpaper && report && (
        <>
          <Badge tone={report.ready ? "good" : "warning"}>
            {report.ready ? "Checks passed" : "Review needed"}
          </Badge>
          <dl className="review-summary">
            <dt>Bank rows still in review</dt>
            <dd>{report.unreviewed_bank_rows}</dd>
            <dt>Required close tasks pending</dt>
            <dd>{report.mandatory_tasks_pending}</dd>
            <dt>Prepaid installments pending</dt>
            <dd>{report.prepaid_installments_pending ?? 0}</dd>
            <dt>Receivables, payables and loan controls</dt>
            <dd>{report.controls.balanced ? "Tied out" : "Review needed"}</dd>
            <dt>Tax controls</dt>
            <dd>
              {report.tax_controls.balanced ? "Tied out" : "Review needed"}
            </dd>
          </dl>
          <details className="chart-data">
            <summary>Control account workpapers</summary>
            <p className="report-footnote">
              Control report {report.controls.report_id}
            </p>
            <Table
              caption="Subledger control account balances"
              rows={report.controls.items}
              rowKey={(row) => row.account_id}
              columns={[
                {
                  label: "Control",
                  render: (row) => row.control.replaceAll("_", " "),
                },
                { label: "Account reference", render: (row) => row.account_id },
                {
                  label: "Ledger",
                  render: (row) => money(row.ledger_balance, business.currency),
                },
                {
                  label: "Subledger",
                  render: (row) =>
                    money(row.subledger_balance, business.currency),
                },
                {
                  label: "Difference",
                  render: (row) => money(row.difference, business.currency),
                },
              ]}
            />
            <p className="report-footnote">
              Tax report {report.tax_controls.report_id}
            </p>
            <Table
              caption="Tax control account balances"
              rows={report.tax_controls.items}
              rowKey={(row) => row.account_id}
              columns={[
                { label: "Account", render: (row) => row.name },
                {
                  label: "Ledger",
                  render: (row) =>
                    money(row.closing_debit_minus_credit, business.currency),
                },
                {
                  label: "Source balance",
                  render: (row) => money(row.source_balance, business.currency),
                },
                {
                  label: "Difference",
                  render: (row) => money(row.difference, business.currency),
                },
                {
                  label: "Source references",
                  render: (row) => row.source_ids.join(", ") || "—",
                },
              ]}
            />
          </details>
          {report.limitations.map((limitation) => (
            <p key={limitation} className="muted">
              {limitation}
            </p>
          ))}
          <p className="muted">
            Captured{" "}
            {new Intl.DateTimeFormat(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
              timeZone: business.timezone,
            }).format(new Date(workpaper.captured_at))}{" "}
            ({business.timezone})
          </p>
          <p className="report-footnote">Report {report.report_id}</p>
        </>
      )}
    </section>
  );
}
