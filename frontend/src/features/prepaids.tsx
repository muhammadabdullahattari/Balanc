import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
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

export function Prepaids() {
  const business = useBusiness();
  const access = useAccess();
  const allowed = business.p2_prepaids_available === true;
  const writer = ["owner", "accountant", "bookkeeper"].some(
    (role) => access?.role === role,
  );
  const day = today(business.timezone);
  const [creating, setCreating] = useState(false);
  const [approval, setApproval] = useState<{
    schedule: string;
    date: string;
    amount: string;
    key: string;
  }>();
  const path = { business_id: business.id };
  const prefix = [...keys.business(business.id), "prepaids"];
  const list = useQuery({
    queryKey: prefix,
    enabled: allowed,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/prepaids", {
          signal,
          params: { path },
        }),
      ),
  });
  const sources = useQuery({
    queryKey: [...prefix, "sources"],
    enabled: allowed && creating,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/prepaids/sources", {
          signal,
          params: { path },
        }),
      ),
  });
  const accounts = useQuery({
    queryKey: [...prefix, "accounts"],
    enabled: allowed && creating,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const refresh = () =>
    void queryClient.invalidateQueries({
      queryKey: keys.business(business.id),
    });
  const create = useMutation({
    mutationFn: (v: FormData) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/prepaids", {
          params: { path },
          body: {
            source_line_id: String(v.get("source")),
            expense_account_id: String(v.get("expense")),
            description: String(v.get("description")).trim(),
            first_month: String(v.get("month")) + "-01",
            months: Number(v.get("months")),
          },
        }),
      ),
    onSuccess: () => {
      setCreating(false);
      refresh();
    },
  });
  const recognize = useMutation({
    mutationFn: async (item: NonNullable<typeof approval>) => {
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      return result(
        api.POST(
          "/api/v1/businesses/{business_id}/prepaids/{schedule_id}/recognize",
          {
            params: { path: { ...path, schedule_id: item.schedule } },
            body: {
              expected_version: current.version,
              idempotency_key: item.key,
              due_date: item.date,
            },
          },
        ),
      );
    },
    onSuccess: () => {
      setApproval(undefined);
      refresh();
    },
  });
  if (!allowed)
    return (
      <Empty title="Prepaid schedules are unavailable in this workspace." />
    );
  return (
    <>
      <PageHeading
        title="Prepaid expense schedules"
        description="Schedule a posted prepaid cost and approve monthly recognition."
        actions={
          writer && (
            <Button
              onClick={() => {
                create.reset();
                setCreating(true);
              }}
            >
              Prepare schedule
            </Button>
          )
        }
      />
      <ErrorNotice error={list.error} />
      {list.isPending ? (
        <Loading />
      ) : !list.data?.items.length ? (
        <section className="panel">
          <Empty title="No prepaid schedules yet" />
        </section>
      ) : (
        list.data.items.map((schedule) => {
          const next = schedule.installments.find((item) => !item.journal_id);
          return (
            <section className="panel settings-section" key={schedule.id}>
              <h2>{schedule.description}</h2>
              <Table
                caption={`Installments for ${schedule.description}`}
                rows={schedule.installments}
                rowKey={(item) => item.due_date}
                columns={[
                  { label: "Month end", render: (item) => item.due_date },
                  {
                    label: "Expense",
                    render: (item) => money(item.amount, business.currency),
                  },
                  {
                    label: "Status",
                    render: (item) =>
                      item.journal_id
                        ? "Posted"
                        : item.due_date <= day
                          ? "Due"
                          : "Scheduled",
                  },
                  {
                    label: "Action",
                    render: (item) =>
                      writer &&
                      item === next &&
                      item.due_date <= day && (
                        <Button
                          permission="post"
                          variant="secondary"
                          onClick={() => {
                            recognize.reset();
                            setApproval({
                              schedule: schedule.id,
                              date: item.due_date,
                              amount: item.amount,
                              key: crypto.randomUUID(),
                            });
                          }}
                        >
                          Review recognition
                        </Button>
                      ),
                  },
                ]}
              />
            </section>
          );
        })
      )}
      <Modal
        open={creating}
        onClose={() => {
          if (!create.isPending) setCreating(false);
        }}
        title="Prepare a prepaid schedule"
        description="Select a posted prepaid asset debit. A different authorized member approves each installment."
      >
        <ErrorNotice error={create.error || sources.error || accounts.error} />
        <form
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(new FormData(event.currentTarget));
          }}
        >
          <Field label="Posted prepaid asset debit">
            <select name="source" required defaultValue="">
              <option value="">Choose the prepaid source</option>
              {sources.data?.items.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.accounting_date} · {item.account_name} ·{" "}
                  {money(item.amount, business.currency)} · {item.memo}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Expense account">
            <select name="expense" required defaultValue="">
              <option value="">Choose an expense account</option>
              {accounts.data?.items
                .filter(
                  (item) =>
                    item.active && item.kind === "expense" && !item.control,
                )
                .map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Description">
            <input name="description" required maxLength={200} />
          </Field>
          <Field label="First recognition month">
            <input
              name="month"
              type="month"
              required
              defaultValue={day.slice(0, 7)}
            />
          </Field>
          <Field label="Number of months">
            <input
              name="months"
              type="number"
              required
              min={2}
              max={36}
              defaultValue={12}
            />
          </Field>
          <Button busy={create.isPending}>Save schedule</Button>
        </form>
      </Modal>
      <Modal
        open={!!approval}
        onClose={() => {
          if (!recognize.isPending) setApproval(undefined);
        }}
        title="Approve prepaid recognition"
        description="Approval posts the expense and reduces the prepaid asset for this installment."
      >
        <ErrorNotice error={recognize.error} />
        {approval && (
          <p>
            {approval.date} · {money(approval.amount, business.currency)}
          </p>
        )}
        <Button
          permission="post"
          busy={recognize.isPending}
          onClick={() => {
            if (approval) recognize.mutate(approval);
          }}
        >
          Approve and post
        </Button>
      </Modal>
    </>
  );
}
