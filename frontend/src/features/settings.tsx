import { GoogleSignIn } from "./google-signin";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result, clearSession } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  PageHeading,
  SectionTabs,
  Table,
} from "../components/ui";
import { useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Settings() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [section, setSection] = useState("business");
  const members = useQuery({
    queryKey: [...keys.business(b.id), "members"],
    enabled: section === "team",
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/memberships", {
          signal,
          params: { path },
        }),
      ),
  });
  const invite = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/invitations", {
          params: { path },
          body: {
            email: String(v.get("email")),
            role: v.get(
              "role",
            ) as components["schemas"]["InviteCreate"]["role"],
          },
        }),
      );
    },
  });
  const privacy = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/privacy-requests", {
          params: { path },
          body: {
            kind: v.get("kind") === "deletion" ? "deletion" : "export",
            reason: String(v.get("reason")),
          },
        }),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "privacy"],
      });
    },
  });
  const requests = useQuery({
    queryKey: [...keys.business(b.id), "privacy"],
    enabled: section === "privacy",
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/privacy-requests", {
          signal,
          params: { path },
        }),
      ),
  });
  const logoutAll = useMutation({
    mutationFn: () => result(api.POST("/api/v1/auth/logout-all")),
    onSuccess: () => {
      clearSession();
      queryClient.setQueryData(keys.session, null);
    },
  });
  const [googleLinked, setGoogleLinked] = useState(false);
  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE SETTINGS"
        title="Workspace settings"
        description="Manage your accounting context, team access and privacy requests."
      />
      <SectionTabs
        id="settings"
        label="Settings sections"
        value={section}
        onChange={setSection}
        items={[
          ["business", "Business"],
          ["team", "Team"],
          ["security", "Security"],
          ["privacy", "Privacy"],
        ]}
      />
      <section
        className="panel settings-section"
        id="settings-panel"
        role="tabpanel"
        aria-labelledby={`settings-${section}`}
        tabIndex={0}
      >
        {section === "business" && (
          <>
            <h2>{b.name}</h2>
            <p className="muted">
              Changes to accounting policy require an effective date and
              authorized review.
            </p>
            <dl className="review-summary">
              <dt>Base currency</dt>
              <dd>{b.currency}</dd>
              <dt>Accounting basis</dt>
              <dd>{b.basis}</dd>
              <dt>Timezone</dt>
              <dd>{b.timezone}</dd>
              <dt>Jurisdiction</dt>
              <dd>{b.jurisdiction}</dd>
              <dt>Fiscal start month</dt>
              <dd>{b.fiscal_month}</dd>
              <dt>Policy version</dt>
              <dd>{b.policy_version}</dd>
            </dl>
          </>
        )}
        {section === "team" && (
          <>
            <h2>The right people. The right access.</h2>
            <ErrorNotice error={members.error || invite.error} />
            {members.data && (
              <Table
                caption="Business members"
                rows={members.data.items}
                rowKey={(m) => m.user_id}
                columns={[
                  { label: "Member reference", render: (m) => m.user_id },
                  { label: "Role", render: (m) => <Badge>{m.role}</Badge> },
                  {
                    label: "Can post",
                    render: (m) => (m.can_post ? "Yes" : "No"),
                  },
                  {
                    label: "Status",
                    render: (m) => (m.active ? "Active" : "Inactive"),
                  },
                ]}
              />
            )}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                invite.mutate(e.currentTarget);
              }}
            >
              <h3>Invite a teammate</h3>
              <div className="form-grid">
                <Field label="Email">
                  <input name="email" type="email" required />
                </Field>
                <Field label="Role">
                  <select name="role">
                    {[
                      "accountant",
                      "bookkeeper",
                      "reviewer",
                      "auditor",
                      "owner",
                    ].map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <Button permission="invite" busy={invite.isPending}>
                Send invitation
              </Button>
              {invite.isSuccess && (
                <p role="status" className="success-text">
                  Invitation sent. The link is valid for 48 hours.
                </p>
              )}
            </form>
          </>
        )}
        {section === "security" && (
          <>
            <h2>Keep your workspace protected.</h2>
            <h3>Link your Google account</h3>
            {googleLinked && (
              <p role="status">
                Google account linked. You can use Google with your LedgerPilot
                authenticator.
              </p>
            )}
            <GoogleSignIn mode="link" onLinked={() => setGoogleLinked(true)} />

            <p className="muted">
              Signing out everywhere revokes all your active sessions.
            </p>
            <ErrorNotice error={logoutAll.error} />
            <Button
              variant="danger"
              busy={logoutAll.isPending}
              onClick={() => {
                if (
                  window.confirm(
                    "Sign out of all sessions, including this one?",
                  )
                )
                  logoutAll.mutate();
              }}
            >
              Sign out everywhere
            </Button>
          </>
        )}
        {section === "privacy" && (
          <>
            <h2>Your data, with a clear process.</h2>
            <p className="muted">
              Requests are reviewed independently. Accounting retention and
              legal holds may apply; submitting a request does not erase
              financial records.
            </p>
            <ErrorNotice error={privacy.error || requests.error} />
            <form
              onSubmit={(e) => {
                e.preventDefault();
                privacy.mutate(e.currentTarget);
              }}
            >
              <Field label="Request type">
                <select name="kind">
                  <option value="export">Export my data</option>
                  <option value="deletion">Request deletion review</option>
                </select>
              </Field>
              <Field label="Reason">
                <textarea name="reason" required maxLength={1000} />
              </Field>
              <Button busy={privacy.isPending}>Submit request</Button>
              {privacy.isSuccess && (
                <p role="status">Your request has been recorded for review.</p>
              )}
            </form>
            {requests.data && (
              <Table
                caption="Your privacy requests"
                rows={requests.data.items}
                rowKey={(r) => r.id}
                columns={[
                  { label: "Request", render: (r) => r.kind },
                  { label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { label: "Reason", render: (r) => r.reason },
                ]}
              />
            )}
          </>
        )}
      </section>
    </>
  );
}
