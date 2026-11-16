import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  money,
  Table,
} from "../components/ui";
import { useAccess, useBusiness } from "./context";
import type { components } from "../api/generated/schema";

type Scope = {
  ruleId: string;
  version: number;
  start: string;
  end: string;
  cap: string;
};
type Preview = {
  status: string;
  preview_hash: string;
  candidates: {
    id: string;
    date: string;
    description: string;
    amount: string;
  }[];
  flagged_amount_count: number;
  flagged_exclusion_count: number;
};

export function ControlledPosting({
  rules,
  onClose,
}: {
  rules: components["schemas"]["RuleSnapshot"][];
  onClose: () => void;
}) {
  const business = useBusiness();
  const access = useAccess();
  const path = { business_id: business.id };
  const key = [...keys.business(business.id), "controlled-posting"];
  const [confirmed, setConfirmed] = useState(false);
  const status = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/bank-rules/controlled-posting/control",
          { signal, params: { path } },
        ),
      ),
  });
  const toggle = useMutation({
    mutationFn: (paused: boolean) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/bank-rules/controlled-posting/pause",
          { params: { path, query: { paused } } },
        ),
      ),
    onSuccess: (value) => {
      queryClient.setQueryData(key, value);
    },
  });
  const run = (scope: Scope, hash?: string) =>
    result(
      api.POST(
        "/api/v1/businesses/{business_id}/bank-rules/{rule_id}/controlled-posting",
        {
          params: {
            path: { ...path, rule_id: scope.ruleId },
            query: {
              expected_rule_version: scope.version,
              start: scope.start,
              end: scope.end,
              max_amount: scope.cap,
              dry_run: !hash,
              expected_preview_hash: hash,
            },
          },
        },
      ),
    );
  const preview = useMutation({
    mutationFn: async (scope: Scope) => (await run(scope)) as Preview,
  });
  const post = useMutation({
    mutationFn: ({ scope, hash }: { scope: Scope; hash: string }) =>
      run(scope, hash),
    onSettled: () => {
      preview.reset();
      setConfirmed(false);
      void queryClient.invalidateQueries({
        queryKey: keys.business(business.id),
      });
    },
  });
  const busy = toggle.isPending || preview.isPending || post.isPending;
  const paused = status.data?.paused;
  const approved = rules.filter((rule) => rule.active && rule.id);
  const data = preview.data;
  const scope = preview.variables;
  const currentRule = approved.some(
    (rule) => rule.id === scope?.ruleId && rule.version === scope?.version,
  );
  const ready = paused === false && !status.isError && !status.isFetching;
  const reset = () => {
    preview.reset();
    post.reset();
    setConfirmed(false);
  };

  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Controlled Posting"
      description="Review eligible bank transactions before posting them to the general ledger."
    >
      {status.isPending && <Loading />}
      <ErrorNotice
        error={status.error}
        retry={
          status.isError
            ? () => {
                void status.refetch();
              }
            : undefined
        }
      />
      <ErrorNotice error={toggle.error || preview.error || post.error} />
      {typeof paused === "boolean" && (
        <section aria-label="Workspace posting status">
          <p role="status">
            Workspace status: <Badge>{paused ? "Paused" : "Active"}</Badge>
          </p>
          <Button
            variant="secondary"
            disabled={busy || status.isFetching || status.isError}
            onClick={() => {
              reset();
              toggle.mutate(!paused);
            }}
          >
            {paused ? "Resume posting" : "Pause posting"}
          </Button>
        </section>
      )}
      {!approved.length && (
        <p>
          Review and activate a bank rule before running controlled posting.
        </p>
      )}
      <form
        onChange={reset}
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready || busy) return;
          const form = new FormData(event.currentTarget);
          const rule = approved.find((item) => item.id === form.get("rule"));
          if (!rule?.id) return;
          const start = String(form.get("start"));
          const end = String(form.get("end"));
          if (end < start) {
            const field = event.currentTarget.elements.namedItem(
              "end",
            ) as HTMLInputElement;
            field.setCustomValidity(
              "Choose an end date on or after the start date.",
            );
            field.reportValidity();
            return;
          }
          reset();
          preview.mutate({
            ruleId: rule.id,
            version: rule.version,
            start,
            end,
            cap: String(form.get("cap")),
          });
        }}
      >
        <fieldset disabled={busy || !ready || !approved.length}>
          <Field label="Approved bank rule">
            <select name="rule" required defaultValue="">
              <option value="">Choose a rule</option>
              {approved.map((rule) => (
                <option key={rule.id} value={rule.id ?? ""}>
                  {rule.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="form-grid">
            <Field label="Start date">
              <input
                name="start"
                type="date"
                required
                onChange={(event) => {
                  const end = event.currentTarget.form?.elements.namedItem(
                    "end",
                  ) as HTMLInputElement | null;
                  end?.setCustomValidity("");
                }}
              />
            </Field>
            <Field label="End date">
              <input
                name="end"
                type="date"
                required
                onChange={(event) => event.currentTarget.setCustomValidity("")}
              />
            </Field>
          </div>
          <Field label={`Maximum per transaction (${business.currency})`}>
            <input
              name="cap"
              type="number"
              min="0.000001"
              max="1000000"
              step="0.000001"
              defaultValue="500"
              required
            />
          </Field>
          <Button type="submit" busy={preview.isPending}>
            Run dry run
          </Button>
        </fieldset>
      </form>
      {data?.status === "workspace_paused" && (
        <p role="status">
          Posting is paused. Refresh the workspace status before continuing.
        </p>
      )}
      {data?.status === "dry_run" && (
        <section aria-label="Dry run results">
          <p role="status">
            {data.candidates.length} eligible. {data.flagged_amount_count} above
            the cap. {data.flagged_exclusion_count} excluded. Each run checks up
            to 100 matching transactions.
          </p>
          {data.candidates.length > 0 && (
            <>
              <Table
                caption="Transactions eligible for posting"
                rows={data.candidates}
                rowKey={(row) => row.id}
                columns={[
                  { label: "Date", render: (row) => row.date },
                  { label: "Description", render: (row) => row.description },
                  {
                    label: "Amount",
                    render: (row) => money(row.amount, business.currency),
                  },
                ]}
              />
              {!currentRule && (
                <p role="status">The rule changed. Run a new dry run.</p>
              )}
              {(access?.role === "accountant" || access?.can_post) && (
                <>
                  <label>
                    <input
                      type="checkbox"
                      checked={confirmed}
                      disabled={busy || !ready || !currentRule}
                      onChange={(event) =>
                        setConfirmed(event.currentTarget.checked)
                      }
                    />{" "}
                    I reviewed these transactions and want to post them to the
                    general ledger.
                  </label>
                  <Button
                    disabled={
                      !confirmed ||
                      !ready ||
                      !currentRule ||
                      busy ||
                      !data.preview_hash
                    }
                    busy={post.isPending}
                    onClick={() => {
                      if (scope && confirmed && data.preview_hash)
                        post.mutate({ scope, hash: data.preview_hash });
                    }}
                  >
                    Post {data.candidates.length} transactions
                  </Button>
                </>
              )}
            </>
          )}
        </section>
      )}
      {post.isSuccess && (
        <p role="status">
          Posted {String(post.data.posted_count)} transactions to the general
          ledger.
        </p>
      )}
    </Modal>
  );
}
