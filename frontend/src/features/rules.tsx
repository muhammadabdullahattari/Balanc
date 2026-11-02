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
  money,
  Badge,
} from "../components/ui";
import { useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Rules() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const [selected, setSelected] =
    useState<components["schemas"]["RuleSnapshot"]>();
  const rules = useQuery({
    queryKey: [...keys.business(b.id), "rules"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/bank-rules", {
          signal,
          params: { path },
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
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };
  const create = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/bank-rules", {
          params: { path },
          body: {
            name: String(v.get("name")),
            contains: String(v.get("contains")),
            account_id: String(v.get("account")),
            priority: Number(v.get("priority")),
          },
        }),
      );
    },
    onSuccess: refresh,
  });
  const preview = useQuery({
    queryKey: [
      ...keys.business(b.id),
      "rule-preview",
      selected?.id,
      selected?.version,
    ],
    enabled: !!selected?.id,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/bank-rules/{rule_id}/preview",
          { signal, params: { path: { ...path, rule_id: selected!.id! } } },
        ),
      ),
  });
  const activate = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/bank-rules/{rule_id}/activation",
          {
            params: { path: { ...path, rule_id: selected!.id! } },
            body: {
              active: !selected!.active,
              expected_version: selected!.version,
              preview_hash: preview.data?.preview_hash,
            },
          },
        ),
      ),
    onSuccess: () => {
      setSelected(undefined);
      refresh();
    },
  });
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="REVIEW RULES"
        title="Consistent suggestions. Human control."
        description="Create description-based category rules and review affected rows before activation."
      />
      <ErrorNotice error={rules.error || accounts.error || create.error} />
      <section className="panel">
        {rules.data?.items.length ? (
          <Table
            caption="Category rules"
            rows={rules.data.items}
            rowKey={(r) => r.id!}
            columns={[
              { label: "Name", render: (r) => r.name },
              { label: "Description contains", render: (r) => r.contains },
              { label: "Priority", render: (r) => r.priority },
              {
                label: "Status",
                render: (r) => (
                  <Badge>{r.active ? "Active" : "Inactive"}</Badge>
                ),
              },
              {
                label: "Review",
                render: (r) => (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setSelected(r);
                      activate.reset();
                    }}
                  >
                    Preview & manage
                  </Button>
                ),
              },
            ]}
          />
        ) : (
          <Empty title="Build your first review rule." />
        )}
      </section>
      <section className="panel settings-section">
        <h2>Create a rule</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(e.currentTarget);
          }}
        >
          <Field label="Rule name">
            <input name="name" required maxLength={160} />
          </Field>
          <Field label="Bank description contains">
            <input name="contains" required minLength={2} maxLength={120} />
          </Field>
          <Field label="Suggested account">
            <select name="account" required>
              <option value="">Choose account</option>
              {accounts.data?.items
                .filter((a) => a.active)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Priority (0–1000)">
            <input
              name="priority"
              type="number"
              min={0}
              max={1000}
              required
              defaultValue={100}
            />
          </Field>
          <Button busy={create.isPending}>Create inactive rule</Button>
          {create.isSuccess && (
            <p role="status">
              Rule created. Review the affected rows before activating.
            </p>
          )}
        </form>
      </section>
      <Modal
        open={!!selected}
        onClose={() => {
          if (!activate.isPending) setSelected(undefined);
        }}
        title={selected?.name || "Review rule"}
        description="Activation changes suggestions; bank entries still require individual review."
      >
        <ErrorNotice error={preview.error || activate.error} />
        {preview.data && (
          <>
            <Table
              caption="Affected bank rows"
              rows={preview.data.rows}
              rowKey={(r) => r.id}
              columns={[
                { label: "Bank row reference", render: (r) => r.id },
                { label: "Amount", render: (r) => money(r.amount, b.currency) },
              ]}
            />
            <Button busy={activate.isPending} onClick={() => activate.mutate()}>
              {selected?.active ? "Deactivate rule" : "Activate reviewed rule"}
            </Button>
          </>
        )}
      </Modal>
    </>
  );
}
