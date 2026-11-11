import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { Button, ErrorNotice, Field, Loading, Table } from "../components/ui";
import { useAccess, useBusiness } from "./context";

export function EmailOperations() {
  const business = useBusiness();
  const access = useAccess();
  const administrator =
    access?.role === "owner" || access?.role === "accountant";
  const path = { business_id: business.id };
  const key = [...keys.business(business.id), "email-operations"];
  const overview = useQuery({
    queryKey: key,
    refetchInterval: administrator ? false : 60_000,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/email-operations", {
          signal,
          params: { path },
        }),
      ),
  });
  const members = useQuery({
    queryKey: [...keys.business(business.id), "members"],
    enabled: administrator,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/memberships", {
          signal,
          params: { path },
        }),
      ),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({
      queryKey: keys.business(business.id),
    });
  };
  const feedback = useMutation({
    mutationFn: (fields: FormData) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/email-operations/jobs/{job_id}/feedback",
          {
            params: { path: { ...path, job_id: String(fields.get("job")) } },
            body: {
              kind:
                fields.get("kind") === "complained" ? "complained" : "bounced",
            },
          },
        ),
      ),
    onSuccess: refresh,
  });
  const grant = useMutation({
    mutationFn: (fields: FormData) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/email-operations/grants", {
          params: { path },
          body: {
            reviewer_id: String(fields.get("reviewer")),
            hours: Number(fields.get("hours")),
          },
        }),
      ),
    onSuccess: refresh,
  });
  const revoke = useMutation({
    mutationFn: (grantId: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/email-operations/grants/{grant_id}/revoke",
          {
            params: { path: { ...path, grant_id: grantId } },
          },
        ),
      ),
    onSuccess: refresh,
  });
  const overviewData = overview.isError ? undefined : overview.data;
  const reviewers =
    members.data?.items.filter(
      (member) => member.active && member.role === "reviewer",
    ) ?? [];
  const eligible =
    overviewData?.attention.filter((job) =>
      ["sent", "uncertain", "failed"].includes(job.state),
    ) ?? [];
  const time = (value: string) =>
    new Intl.DateTimeFormat(undefined, {
      timeZone: business.timezone,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  return (
    <section className="panel settings-section">
      <h2>Workspace email operations</h2>
      <ErrorNotice
        error={
          overview.error ||
          members.error ||
          feedback.error ||
          grant.error ||
          revoke.error
        }
      />
      {overview.isPending && <Loading />}
      {overviewData && (
        <>
          <p className="muted">Email jobs created in the last seven days.</p>
          <dl className="review-summary">
            {[
              "queued",
              "sending",
              "sent",
              "failed",
              "uncertain",
              "suppressed",
            ].map((state) => (
              <div key={state}>
                <dt>{state === "sent" ? "Accepted by SMTP" : state}</dt>
                <dd>{overviewData.counts[state] ?? 0}</dd>
              </div>
            ))}
          </dl>
          {overviewData.attention.length ? (
            <Table
              caption="Recent email jobs"
              rows={overviewData.attention}
              rowKey={(job) => job.id}
              columns={[
                { label: "Job", render: (job) => job.id },
                { label: "Created", render: (job) => time(job.created_at) },
                {
                  label: "Type",
                  render: (job) => job.kind.replaceAll("_", " "),
                },
                {
                  label: "State",
                  render: (job) =>
                    job.state === "sent" ? "Accepted by SMTP" : job.state,
                },
                { label: "Attempts", render: (job) => job.attempts },
                { label: "Outcome", render: (job) => job.outcome_code ?? "—" },
                {
                  label: "Feedback",
                  render: (job) => job.feedback.join(", ") || "—",
                },
              ]}
            />
          ) : (
            <p className="muted">No recent jobs in this view.</p>
          )}
          {administrator && (
            <>
              <h3>Record confirmed delivery feedback</h3>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  feedback.mutate(new FormData(event.currentTarget));
                }}
              >
                <Field label="Email job ID">
                  <input
                    name="job"
                    list="recent-email-jobs"
                    required
                    pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                  />
                  <datalist id="recent-email-jobs">
                    {eligible.map((job) => (
                      <option key={job.id} value={job.id}>
                        {job.kind.replaceAll("_", " ")}
                      </option>
                    ))}
                  </datalist>
                </Field>
                <Field label="Confirmed feedback">
                  <select name="kind">
                    <option value="bounced">Permanent bounce</option>
                    <option value="complained">Spam complaint</option>
                  </select>
                </Field>
                <label>
                  <input type="checkbox" required /> Suppress future workspace
                  email for this recipient
                </label>
                <Button busy={feedback.isPending}>Record feedback</Button>
                {feedback.isSuccess && (
                  <p role="status">
                    Feedback recorded. Future sends are suppressed.
                  </p>
                )}
              </form>
              <h3>Delegate email diagnostics</h3>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  grant.mutate(new FormData(event.currentTarget));
                }}
              >
                <Field label="Workspace reviewer">
                  <select name="reviewer" required>
                    <option value="">Choose an active reviewer</option>
                    {reviewers.map((member) => (
                      <option key={member.user_id} value={member.user_id}>
                        {member.user_id}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Access duration in hours">
                  <input
                    name="hours"
                    type="number"
                    min={1}
                    max={24}
                    defaultValue={4}
                    required
                  />
                </Field>
                <Button busy={grant.isPending} disabled={!reviewers.length}>
                  Grant diagnostics access
                </Button>
              </form>
              {overviewData.grants.length > 0 && (
                <Table
                  caption="Recent diagnostics grants"
                  rows={overviewData.grants}
                  rowKey={(item) => item.id}
                  columns={[
                    { label: "Reviewer", render: (item) => item.reviewer_id },
                    {
                      label: "Expires",
                      render: (item) => time(item.expires_at),
                    },
                    {
                      label: "Access",
                      render: (item) =>
                        item.revoked_at ? (
                          "Revoked"
                        ) : Date.parse(item.expires_at) <= Date.now() ? (
                          "Expired"
                        ) : (
                          <Button
                            variant="secondary"
                            busy={revoke.isPending}
                            onClick={() => revoke.mutate(item.id)}
                          >
                            Revoke
                          </Button>
                        ),
                    },
                  ]}
                />
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
