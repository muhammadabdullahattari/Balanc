import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { Button, ErrorNotice, Field, Table } from "../components/ui";
import { today, useBusiness, useAccess } from "./context";
import type { components } from "../api/generated/schema";

const categories = [
  "journals",
  "trade_documents",
  "evidence",
  "bank_records",
  "audit_records",
] as const;
export function RetentionControls() {
  const business = useBusiness();
  const access = useAccess();
  const path = { business_id: business.id };
  const key = [...keys.business(business.id), "retention"];
  const allowed =
    business.p2_retention_available &&
    (access?.role === "owner" || access?.role === "accountant");
  const state = useQuery({
    queryKey: key,
    enabled: allowed,
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/retention", {
          params: { path },
          signal,
        }),
      ),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: key });
  };
  const policy = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const data = new FormData(form);
      const category = String(
        data.get("category"),
      ) as components["schemas"]["PolicyCreate"]["category"];
      const jurisdiction = String(data.get("jurisdiction"))
        .trim()
        .toUpperCase();
      const previous = state.data?.policies.find(
        (p) => p.category === category && p.jurisdiction === jurisdiction,
      );
      return result(
        api.POST("/api/v1/businesses/{business_id}/retention/policies", {
          params: { path },
          body: {
            category,
            jurisdiction,
            years: Number(data.get("years")),
            reference: String(data.get("reference")),
            expected_version: previous?.version ?? 0,
          },
        }),
      );
    },
    onSuccess: refresh,
  });
  const hold = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const data = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/retention/holds", {
          params: { path },
          body: {
            category: String(
              data.get("category"),
            ) as components["schemas"]["HoldCreate"]["category"],
            reason: String(data.get("reason")),
          },
        }),
      );
    },
    onSuccess: refresh,
  });
  const release = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/holds/{hold_id}/release",
          { params: { path: { ...path, hold_id: id } }, body: { reason } },
        ),
      ),
    onSuccess: refresh,
  });
  const preview = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const data = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/retention/preview", {
          params: { path },
          body: {
            category: String(
              data.get("category"),
            ) as components["schemas"]["EligibilityInput"]["category"],
            jurisdiction: String(data.get("jurisdiction")),
            record_date: String(data.get("record_date")),
            as_of: String(data.get("as_of")),
          },
        }),
      );
    },
  });
  if (!allowed) return null;
  return (
    <section aria-label="Retention and legal holds">
      <h2>Retention and legal holds</h2>
      <p className="muted">
        Financial records default to ten years from the record date. Record
        approved exceptions here. A legal hold prevents disposal review
        regardless of age. These controls do not delete records or backups.
      </p>
      <ErrorNotice
        error={
          state.error ||
          policy.error ||
          hold.error ||
          release.error ||
          preview.error
        }
      />
      {state.isPending && <p role="status">Loading retention controls…</p>}
      {state.data && (
        <>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              preview.mutate(e.currentTarget);
            }}
          >
            <h3>Check retention eligibility</h3>
            <Field label="Preview record class">
              <select name="category">
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Preview jurisdiction">
              <input
                name="jurisdiction"
                defaultValue="US"
                minLength={2}
                maxLength={80}
                required
              />
            </Field>
            <Field label="Record date">
              <input name="record_date" type="date" required />
            </Field>
            <Field label="Assessment date">
              <input
                name="as_of"
                type="date"
                defaultValue={today(business.timezone)}
                required
              />
            </Field>
            <Button busy={preview.isPending}>Check eligibility</Button>
          </form>
          {preview.data && (
            <p role="status">
              Retain until {preview.data.retain_until}.{" "}
              {preview.data.active_hold_ids.length > 0
                ? "An active legal hold prevents disposal review."
                : preview.data.eligible_for_disposal_review
                  ? "Eligible for policy review; deletion is not authorized."
                  : "The retention period has not elapsed."}
            </p>
          )}
          <Table
            caption="Current retention policies"
            rows={state.data.policies}
            rowKey={(p) => p.id}
            columns={[
              {
                label: "Record class",
                render: (p) => p.category.replaceAll("_", " "),
              },
              { label: "Jurisdiction", render: (p) => p.jurisdiction },
              { label: "Years", render: (p) => p.years },
              { label: "Version", render: (p) => p.version },
              { label: "Policy reference", render: (p) => p.reference },
            ]}
          />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              policy.mutate(e.currentTarget);
            }}
          >
            <h3>Record an approved policy</h3>
            <Field label="Record class">
              <select name="category">
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Jurisdiction">
              <input
                name="jurisdiction"
                required
                minLength={2}
                maxLength={80}
                defaultValue="US"
              />
            </Field>
            <Field label="Retention years">
              <input
                name="years"
                type="number"
                min={1}
                max={100}
                defaultValue={10}
                required
              />
            </Field>
            <Field label="Approved policy reference">
              <input name="reference" required maxLength={200} />
            </Field>
            <Button busy={policy.isPending}>Save policy version</Button>
            {policy.isSuccess && <p role="status">Policy version saved.</p>}
          </form>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              hold.mutate(e.currentTarget);
            }}
          >
            <h3>Place a legal hold</h3>
            <Field label="Hold scope">
              <select name="category">
                <option value="all">All financial records</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Hold reason">
              <textarea name="reason" required maxLength={1000} />
            </Field>
            <Button busy={hold.isPending}>Place hold</Button>
            {hold.isSuccess && <p role="status">Legal hold recorded.</p>}
          </form>
          <Table
            caption="Legal hold history"
            rows={state.data.holds}
            rowKey={(h) => h.id}
            columns={[
              {
                label: "Scope",
                render: (h) => h.category.replaceAll("_", " "),
              },
              { label: "Reason", render: (h) => h.reason },
              {
                label: "State",
                render: (h) => (h.released_at ? "Released" : "Active"),
              },
              {
                label: "Release",
                render: (h) =>
                  h.released_at ? (
                    h.release_reason
                  ) : (
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        release.mutate({
                          id: h.id,
                          reason: String(
                            new FormData(e.currentTarget).get("reason"),
                          ),
                        });
                      }}
                    >
                      <Field label="Reason for releasing hold">
                        <input name="reason" required maxLength={1000} />
                      </Field>
                      <Button busy={release.isPending}>Release hold</Button>
                    </form>
                  ),
              },
            ]}
          />
        </>
      )}
    </section>
  );
}
