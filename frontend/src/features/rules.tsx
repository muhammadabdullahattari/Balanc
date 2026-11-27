import { ControlledPosting } from "./controlled-posting";
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
import { useAccess, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Rules() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const access = useAccess();
  const [postingOpen, setPostingOpen] = useState(false);
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
  const suggestions = useQuery({
    queryKey: [...keys.business(b.id), "rule-correction-suggestions"],
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/bank-rules/correction-suggestions",
          { signal, params: { path } },
        ),
      ),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };
  const acceptSuggestion = useMutation({
    mutationFn: (item: {
      descriptor: string;
      suggested_contains: string;
      account_id: string;
    }) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/bank-rules", {
          params: { path },
          body: {
            name: `Learned: ${item.descriptor.slice(0, 60)}`,
            contains: item.suggested_contains.slice(0, 120),
            account_id: item.account_id,
            priority: 100,
          },
        }),
      ),
    onSuccess: refresh,
  });
  const dismissSuggestion = useMutation({
    mutationFn: (item: { descriptor: string; account_id: string }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/bank-rules/correction-suggestions/dismiss",
          {
            params: { path },
            body: {
              descriptor: item.descriptor,
              account_id: item.account_id,
            },
          },
        ),
      ),
    onSuccess: refresh,
  });
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
        title="Bank rules"
        description="Create description-based category rules and review affected rows before activation."
        actions={
          b.p2_insights_available &&
          (access?.role === "owner" || access?.role === "accountant") && (
            <Button variant="secondary" onClick={() => setPostingOpen(true)}>
              Controlled Posting
            </Button>
          )
        }
      />
      {postingOpen && (
        <ControlledPosting
          key={b.id}
          rules={rules.data?.items ?? []}
          onClose={() => setPostingOpen(false)}
        />
      )}
      <ErrorNotice error={rules.error || accounts.error || create.error} />
      {Array.isArray(suggestions.data?.items) &&
        suggestions.data.items.length > 0 && (
          <section
            className="panel settings-section"
            aria-label="Suggested from your corrections"
          >
            <h2>Suggested from your corrections</h2>
            <p className="muted">
              You reclassified matching bank entries at least twice. Create an
              inactive rule to review affected rows before activation, or
              dismiss suggestions you do not want.
            </p>
            <Table
              caption="Suggested category rules from repeated corrections"
              rows={suggestions.data.items}
              rowKey={(s) => `${s.descriptor}:${s.account_id}`}
              columns={[
                {
                  label: "Merchant pattern",
                  render: (s) => <strong>{s.suggested_contains}</strong>,
                },
                {
                  label: "Target account",
                  render: (s) =>
                    accounts.data?.items.find((a) => a.id === s.account_id)
                      ?.name || s.account_id,
                },
                {
                  label: "Repeated corrections",
                  render: (s) => <Badge>{s.sample_count} entries</Badge>,
                },
                {
                  label: "Actions",
                  render: (s) => (
                    <div className="p4-inline-actions">
                      <Button
                        type="button"
                        variant="secondary"
                        busy={acceptSuggestion.isPending}
                        onClick={() => acceptSuggestion.mutate(s)}
                      >
                        Create inactive rule
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        busy={dismissSuggestion.isPending}
                        onClick={() => dismissSuggestion.mutate(s)}
                      >
                        Dismiss
                      </Button>
                    </div>
                  ),
                },
              ]}
            />
          </section>
        )}
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
        {b.p2_insights_available && selected?.id && (
          <RuleShadowEvaluation key={selected.id} ruleId={selected.id} />
        )}
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

function RuleShadowEvaluation({ ruleId }: { ruleId: string }) {
  const business = useBusiness();
  const check = useMutation({
    mutationFn: (form: FormData) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/bank-rules/{rule_id}/shadow-evaluation",
          {
            params: {
              path: { business_id: business.id, rule_id: ruleId },
              query: {
                start: String(form.get("start")),
                end: String(form.get("end")),
                max_amount: String(form.get("cap")),
              },
            },
          },
        ),
      ),
  });
  return (
    <details className="chart-data">
      <summary>Test this rule against reviewed entries</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          check.mutate(new FormData(event.currentTarget));
        }}
      >
        <Field label="Start date">
          <input type="date" name="start" required />
        </Field>
        <Field label="End date">
          <input type="date" name="end" required />
        </Field>
        <Field label={`Maximum transaction amount (${business.currency})`}>
          <input
            name="cap"
            inputMode="decimal"
            required
            pattern="[0-9]+(\.[0-9]{1,6})?"
          />
        </Field>
        <Button variant="secondary" busy={check.isPending}>
          Evaluate rule
        </Button>
      </form>
      <ErrorNotice error={check.error} />
      {check.data && (
        <>
          <p role="status">
            Rule version {check.data.rule_version} agreed with{" "}
            {check.data.matched} of {check.data.evaluated} eligible reviewed
            entries.
          </p>
          {check.data.mismatched_row_ids.length > 0 && (
            <ul aria-label="Bank rows where the rule disagreed">
              {check.data.mismatched_row_ids.map((id) => (
                <li key={id}>{id}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </details>
  );
}
