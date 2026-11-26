import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, keys, result } from "../api/runtime";
import { Button, ErrorNotice, Table, humanize, money } from "../components/ui";
import type { components } from "../api/generated/schema";
import { useAccess, useBusiness } from "./context";

export function ClosePackages({ periodId }: { periodId: string }) {
  const business = useBusiness();
  const access = useAccess();
  const client = useQueryClient();
  const path = { business_id: business.id };
  const key = [...keys.business(business.id), "close-packages", periodId];
  const allowed = access?.role === "owner" || access?.role === "accountant";
  const packages = useQuery({
    queryKey: key,
    enabled: !!periodId && allowed,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/close-packages", {
          signal,
          params: { path, query: { period_id: periodId } },
        }),
      ),
  });
  const refresh = () => client.invalidateQueries({ queryKey: key });
  const prepare = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/close-packages", {
          params: { path, query: { period_id: periodId } },
        }),
      ),
    onSuccess: refresh,
  });
  const sign = useMutation({
    mutationFn: (packageId: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/close-packages/{package_id}/review",
          {
            params: { path: { ...path, package_id: packageId } },
          },
        ),
      ),
    onSuccess: refresh,
  });
  if (!allowed) return null;
  return (
    <section className="panel" aria-label="Period-close workpapers">
      <h2>Period-close workpapers</h2>
      {!periodId ? (
        <p>Select an accounting period to see its packages.</p>
      ) : (
        <>
          <Button disabled={prepare.isPending} onClick={() => prepare.mutate()}>
            {prepare.isPending ? "Preparing…" : "Prepare workpaper package"}
          </Button>
          <ErrorNotice error={packages.error || prepare.error || sign.error} />
          {packages.isPending && <p role="status">Loading workpapers…</p>}
          {prepare.isSuccess && <p role="status">Workpaper package saved.</p>}
          {sign.isSuccess && (
            <p role="status">Independent sign-off recorded.</p>
          )}
          {packages.data?.items.length === 0 && (
            <p>No workpapers saved for this period.</p>
          )}
          {packages.data?.truncated && <p>Showing the latest 50 packages.</p>}
          {packages.data?.items.map(({ package: item, review, can_sign }) => (
            <details key={item.id}>
              <summary>
                {new Date(item.prepared_at).toLocaleString()} ·{" "}
                {review ? "Signed off" : "Awaiting review"}
              </summary>
              <p>
                {item.start} through {item.end} ·{" "}
                {item.locked ? "Locked" : "Open"} at capture
              </p>
              <p>Prepared by {item.prepared_by}</p>
              {review && (
                <p>
                  Signed by {review.reviewed_by} on{" "}
                  {new Date(review.reviewed_at).toLocaleString()}
                </p>
              )}
              <dl>
                <dt>Unreviewed bank transactions</dt>
                <dd>
                  {String(item.readiness.unreviewed_bank_rows ?? "Unavailable")}
                </dd>
                <dt>Pending required close tasks</dt>
                <dd>
                  {String(
                    item.readiness.mandatory_tasks_pending ?? "Unavailable",
                  )}
                </dd>
                <dt>Pending prepaid installments</dt>
                <dd>
                  {String(
                    item.readiness.prepaid_installments_pending ??
                      "Unavailable",
                  )}
                </dd>
              </dl>
              <ul>
                {item.tasks.map((task) => (
                  <li key={String(task.id)}>
                    {String(task.name)} · {humanize(String(task.state))}
                    {task.evidence_id
                      ? ` · Evidence ${String(task.evidence_id)}`
                      : ""}
                    {task.comment ? <p>{String(task.comment)}</p> : null}
                    {task.review_comment ? (
                      <p>{String(task.review_comment)}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
              <details>
                <summary>Ledger and tax tie-outs</summary>
                <TieOuts
                  readiness={item.readiness}
                  currency={business.currency}
                />
              </details>
              {can_sign && (
                <Button
                  disabled={sign.isPending}
                  onClick={() => sign.mutate(item.id)}
                >
                  Record independent sign-off
                </Button>
              )}
            </details>
          ))}
        </>
      )}
    </section>
  );
}

function TieOuts({
  readiness,
  currency,
}: {
  readiness: Record<string, unknown>;
  currency: string;
}) {
  const controls = readiness.controls as
    components["schemas"]["ControlReport"] | undefined;
  const tax = readiness.tax_controls as
    components["schemas"]["TaxSupportReport"] | undefined;
  return (
    <>
      {controls && (
        <Table
          caption="Subledger controls"
          rows={controls.items}
          rowKey={(row) => row.account_id}
          columns={[
            {
              label: "Control",
              render: (row) => row.control.replaceAll("_", " "),
            },
            {
              label: "Ledger",
              render: (row) => money(row.ledger_balance, currency),
            },
            {
              label: "Subledger",
              render: (row) => money(row.subledger_balance, currency),
            },
            {
              label: "Difference",
              render: (row) => money(row.difference, currency),
            },
          ]}
        />
      )}
      {tax && (
        <Table
          caption="Tax controls"
          rows={tax.items}
          rowKey={(row) => row.account_id}
          columns={[
            { label: "Account", render: (row) => row.name },
            {
              label: "Ledger",
              render: (row) => money(row.closing_debit_minus_credit, currency),
            },
            {
              label: "Source balance",
              render: (row) => money(row.source_balance, currency),
            },
            {
              label: "Difference",
              render: (row) => money(row.difference, currency),
            },
            {
              label: "Source references",
              render: (row) => row.source_ids.join(", ") || "—",
            },
          ]}
        />
      )}
    </>
  );
}
