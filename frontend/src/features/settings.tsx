import { GoogleSignIn } from "./google-signin";
import { RetentionControls } from "./retention";
import { AuditShares } from "./audit-access";
import { BillingSettings } from "./billing";
import { QuickBooksImport } from "./quickbooks-import";
import { RolePermissions } from "./role-permissions";
import {
  HumanApprovedSupplierPayments,
  MultichannelReminders,
  SupplierPortalConnectors,
} from "./p3-automation";
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
import { useBusiness, useAccess } from "./context";
import type { components } from "../api/generated/schema";
export function Settings({ initialSection }: { initialSection?: string } = {}) {
  const b = useBusiness();
  const access = useAccess();
  const role = access?.role ?? "";
  const isOwner = role === "owner";
  const canManageTeam = role === "owner" || role === "accountant";
  const path = { business_id: b.id };
  const [section, setSection] = useState(() => {
    if (initialSection) return initialSection;
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      if (
        window.location.pathname.endsWith("/billing") ||
        params.has("billing")
      ) {
        return "billing";
      }
    }
    return "business";
  });
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
  const downloadPrivacy = useMutation({
    mutationFn: async (requestId?: string) => {
      const blob = await result(
        api.GET("/api/v1/businesses/{business_id}/privacy-export", {
          params: { path, query: { request_id: requestId } },
          parseAs: "blob",
        }),
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "ledgerpilot-personal-data.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
  const logoutAll = useMutation({
    mutationFn: () => result(api.POST("/api/v1/auth/logout-all")),
    onSuccess: () => {
      clearSession();
      queryClient.setQueryData(keys.session, null);
    },
  });
  const [googleLinked, setGoogleLinked] = useState(false);
  const session = useQuery({
    queryKey: keys.session,
    queryFn: () => result(api.GET("/api/v1/auth/me")),
  });
  const isGoogleLinked = Boolean(
    googleLinked ||
    (session.data as { google_linked?: boolean } | undefined)?.google_linked,
  );
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
          ["permissions", "Permissions"],
          ["billing", "Billing"],
          ["integrations", "Integrations"],
          ["reminders", "WhatsApp & Slack"],
          ["portals", "Supplier Portals"],
          ["payments", "Supplier Payments"],
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
            {/* All active members can view the team directory */}
            <ErrorNotice error={invite.error} />
            {members.isError && !members.data && (
              <div className="info-callout">
                <p>
                  <strong>Team directory unavailable.</strong> Contact your
                  workspace owner if you believe this is an error.
                </p>
              </div>
            )}
            {members.data && (
              <Table
                caption="Business members"
                rows={members.data.items}
                rowKey={(m) => m.user_id}
                columns={[
                  {
                    label: "Member",
                    render: (m) =>
                      m.email ? (
                        <span className="member-email">{m.email}</span>
                      ) : (
                        <span className="member-id muted" title={m.user_id}>
                          {m.user_id.slice(0, 8)}…
                        </span>
                      ),
                  },
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
            {/* Invite form — owners and accountants with can_invite only */}
            {isOwner || (canManageTeam && access?.can_invite) ? (
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
                        ...(isOwner ? ["owner"] : []),
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
            ) : (
              <div className="info-callout">
                <p>
                  <strong>Invitations are managed by workspace owners.</strong>{" "}
                  Contact your owner to add or remove team members.
                </p>
              </div>
            )}
          </>
        )}
        {section === "billing" && <BillingSettings />}
        {section === "permissions" && <RolePermissions />}
        {section === "integrations" && <QuickBooksImport />}
        {section === "reminders" && <MultichannelReminders />}
        {section === "portals" && <SupplierPortalConnectors />}
        {section === "payments" && <HumanApprovedSupplierPayments />}
        {section === "security" && (
          <>
            <h2>Keep your workspace protected.</h2>
            <h3>Google account sign-in</h3>
            {isGoogleLinked ? (
              <div className="info-callout" role="status">
                <p>
                  <strong>Google account linked.</strong>{" "}
                  {session.data?.email ? `(${session.data.email}) ` : ""}
                  You can use Google with your LedgerPilot authenticator.
                </p>
              </div>
            ) : (
              <GoogleSignIn
                mode="link"
                onLinked={() => {
                  setGoogleLinked(true);
                  void queryClient.invalidateQueries({
                    queryKey: keys.session,
                  });
                }}
              />
            )}

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
            <RetentionControls />
            <AuditShares />
            <h2>Your data, with a clear process.</h2>
            <p className="muted">
              Requests are reviewed independently. Accounting retention and
              legal holds may apply; submitting a request does not erase
              financial records.
            </p>
            <ErrorNotice
              error={privacy.error || requests.error || downloadPrivacy.error}
            />
            <Button
              variant="secondary"
              busy={downloadPrivacy.isPending}
              onClick={() => downloadPrivacy.mutate(undefined)}
            >
              Download my account data
            </Button>
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
                  {
                    label: "Export",
                    render: (r) =>
                      r.kind === "export" && r.status === "export_ready" ? (
                        <Button
                          variant="secondary"
                          disabled={downloadPrivacy.isPending}
                          onClick={() => downloadPrivacy.mutate(r.id)}
                        >
                          Download approved export
                        </Button>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
            )}
          </>
        )}
      </section>
    </>
  );
}
