import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, result, keys, queryClient } from "../api/runtime";
import {
  Field,
  Button,
  PageHeading,
  Table,
  Empty,
  ErrorNotice,
  Modal,
  Badge,
} from "../components/ui";
import { useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Drafts() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const [selected, setSelected] =
    useState<components["schemas"]["SourceDraftRead"]>();
  const rows = useQuery({
    queryKey: [...keys.business(b.id), "source-drafts"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/source-drafts", {
          signal,
          params: { path },
        }),
      ),
  });
  const action = useMutation({
    mutationFn: async (kind: "issue" | "void") => {
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      const options = {
        params: { path: { ...path, draft_id: selected!.id } },
        body: {
          expected_version: selected!.version,
          expected_business_version: current.version,
        },
      };
      return kind === "issue"
        ? result(
            api.POST(
              "/api/v1/businesses/{business_id}/source-drafts/{draft_id}/issue",
              options,
            ),
          )
        : result(
            api.POST(
              "/api/v1/businesses/{business_id}/source-drafts/{draft_id}/void",
              options,
            ),
          );
    },
    onSuccess: () => {
      setSelected(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  const edit = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.PATCH("/api/v1/businesses/{business_id}/source-drafts/{draft_id}", {
          params: { path: { ...path, draft_id: selected!.id } },
          body: {
            expected_version: selected!.version,
            payload: {
              ...selected!.payload,
              number: String(v.get("number")),
              due_date: String(v.get("due")),
              lines: selected!.payload.lines.map((line, i) => ({
                ...line,
                description: String(v.get(`description-${i}`)),
                amount: String(v.get(`amount-${i}`)),
              })),
            },
          },
        }),
      );
    },
    onSuccess: (data) => {
      setSelected(data);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <>
      <PageHeading
        eyebrow="PREPARE & REVIEW"
        title="Draft review"
        description="Review prepared source documents before issuing or voiding them."
      />
      <ErrorNotice error={rows.error} />
      <section className="panel">
        {rows.data?.items.length ? (
          <Table
            caption="Prepared document drafts"
            rows={rows.data.items}
            rowKey={(r) => r.id}
            columns={[
              { label: "Number", render: (r) => r.payload.number },
              { label: "Type", render: (r) => r.payload.kind },
              { label: "State", render: (r) => <Badge>{r.status}</Badge> },
              {
                label: "Review",
                render: (r) => (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setSelected(r);
                      action.reset();
                    }}
                  >
                    Review draft
                  </Button>
                ),
              },
            ]}
          />
        ) : (
          <Empty title="No prepared drafts.">
            Create an invoice or bill and save it as a draft at the review step.
          </Empty>
        )}
      </section>
      <Modal
        open={!!selected}
        onClose={() => {
          if (!action.isPending) setSelected(undefined);
        }}
        title={selected?.payload.number || "Review draft"}
        description="Issuing a draft creates the financial document. The server checks permissions, versions and accounting rules."
      >
        <ErrorNotice error={action.error || edit.error} />
        {selected?.status === "draft" && (
          <details>
            <summary>Edit prepared document</summary>
            <form
              key={selected.version}
              onSubmit={(e) => {
                e.preventDefault();
                edit.mutate(e.currentTarget);
              }}
            >
              <Field label="Document number">
                <input
                  name="number"
                  required
                  defaultValue={selected.payload.number}
                />
              </Field>
              <Field label="Due date">
                <input
                  type="date"
                  name="due"
                  required
                  defaultValue={selected.payload.due_date}
                />
              </Field>
              {selected.payload.lines.map((line, i) => (
                <fieldset key={i}>
                  <legend>Line {i + 1}</legend>
                  <Field label="Description">
                    <input
                      name={`description-${i}`}
                      required
                      defaultValue={line.description}
                    />
                  </Field>
                  <Field label="Amount">
                    <input
                      name={`amount-${i}`}
                      required
                      inputMode="decimal"
                      defaultValue={line.amount}
                    />
                  </Field>
                </fieldset>
              ))}
              <Button busy={edit.isPending}>Save reviewed changes</Button>
            </form>
          </details>
        )}
        {selected && (
          <>
            <dl className="review-summary">
              <dt>Date</dt>
              <dd>{selected.payload.accounting_date}</dd>
              <dt>Due</dt>
              <dd>{selected.payload.due_date}</dd>
              <dt>Prepared by</dt>
              <dd>{selected.prepared_by}</dd>
            </dl>
            <Table
              caption="Draft lines"
              rows={selected.payload.lines}
              rowKey={(l) => l.account_id + l.description}
              columns={[
                { label: "Description", render: (l) => l.description },
                { label: "Amount", render: (l) => l.amount },
                { label: "Tax", render: (l) => l.tax_amount },
              ]}
            />
            <Button
              variant="danger"
              busy={action.isPending}
              disabled={selected.status !== "draft"}
              onClick={() => action.mutate("void")}
            >
              Void draft
            </Button>
            <Button
              permission="post"
              busy={action.isPending}
              disabled={selected.status !== "draft"}
              onClick={() => action.mutate("issue")}
            >
              Approve & issue
            </Button>
          </>
        )}
      </Modal>
    </>
  );
}
