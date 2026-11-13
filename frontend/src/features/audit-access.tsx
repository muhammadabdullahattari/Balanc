import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { Button, ErrorNotice, Field, Table, NextPage } from "../components/ui";
import { useAccess, useBusiness } from "./context";

export function AuditShares() {
  const b = useBusiness();
  const access = useAccess();
  const [cursor, setCursor] = useState<string>();
  const allowed =
    b.p2_audit_available &&
    ["owner", "accountant"].includes(access?.role || "");
  const path = { business_id: b.id };
  const key = [...keys.business(b.id), "audit-grants"];
  const list = useQuery({
    queryKey: [...key, cursor],
    enabled: allowed,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/audit-grants", {
          params: { path, query: { cursor } },
          signal,
        }),
      ),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: key });
  };
  const create = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const data = new FormData(form);
      return result(
        api.POST("/api/v1/businesses/{business_id}/audit-grants", {
          params: { path },
          body: {
            recipient_id: String(data.get("recipient_id")).trim(),
            report_ids: String(data.get("report_ids"))
              .trim()
              .split(/[\s,]+/),
            hours: Number(data.get("hours")),
            reason: String(data.get("reason")),
          },
        }),
      );
    },
    onSuccess: refresh,
  });
  const revoke = useMutation({
    mutationFn: (id: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/audit-grants/{grant_id}/revoke",
          { params: { path: { ...path, grant_id: id } } },
        ),
      ),
    onSuccess: refresh,
  });
  if (!allowed) return null;
  return (
    <section aria-label="Scoped auditor sharing">
      <h2>Share selected audit workpapers</h2>
      <p className="muted">
        Share saved report snapshots with a named LedgerPilot account, without
        adding a workspace member. The recipient must sign in with MFA. Sharing
        does not remove access they already have through a separate membership.
        Downloads already received cannot be recalled.
      </p>
      <ErrorNotice error={list.error || create.error || revoke.error} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(e.currentTarget);
        }}
      >
        <Field label="Recipient account reference">
          <input name="recipient_id" required />
        </Field>
        <Field label="Saved report references (one per line, maximum 20)">
          <textarea name="report_ids" required />
        </Field>
        <Field label="Access duration in hours">
          <input
            name="hours"
            type="number"
            min={1}
            max={720}
            defaultValue={24}
            required
          />
        </Field>
        <Field label="Sharing reason">
          <textarea name="reason" required maxLength={1000} />
        </Field>
        <Button busy={create.isPending}>Grant report access</Button>
        {create.isSuccess && (
          <p role="status">
            Report access recorded. Share the recipient link below.
          </p>
        )}
      </form>
      {list.isPending && <p role="status">Loading report shares…</p>}
      {list.data && (
        <>
          <Table
            caption="Audit report shares"
            rows={list.data.items}
            rowKey={(g) => g.id}
            columns={[
              { label: "Recipient", render: (g) => g.recipient_id },
              {
                label: "Expires",
                render: (g) => new Date(g.expires_at).toLocaleString(),
              },
              {
                label: "Recipient links",
                render: (g) => (
                  <ul>
                    {g.report_ids.map((id) => (
                      <li key={id}>
                        <Link to={`/audit-access?grant=${g.id}&report=${id}`}>
                          Report {id}
                        </Link>
                      </li>
                    ))}
                  </ul>
                ),
              },
              {
                label: "Access",
                render: (g) =>
                  g.revoked_at ? (
                    "Revoked"
                  ) : (
                    <Button
                      variant="secondary"
                      busy={revoke.isPending}
                      onClick={() => revoke.mutate(g.id)}
                    >
                      Revoke access
                    </Button>
                  ),
              },
            ]}
          />
          <NextPage
            cursor={list.data.next_cursor}
            onNext={() => setCursor(list.data?.next_cursor || undefined)}
            onReset={() => setCursor(undefined)}
          />
        </>
      )}
    </section>
  );
}

export function AuditRecipient({ accountId }: { accountId: string }) {
  const params = new URLSearchParams(window.location.search);
  const read = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const data = new FormData(form);
      return result(
        api.GET("/api/v1/audit-access/{grant_id}/reports/{report_id}", {
          params: {
            path: {
              grant_id: String(data.get("grant_id")).trim(),
              report_id: String(data.get("report_id")).trim(),
            },
          },
        }),
      );
    },
  });
  const report = read.data;
  return (
    <main className="panel">
      <h1>Shared audit workpaper</h1>
      <p>
        Your account reference: <code>{accountId}</code>. Give this reference to
        the workspace owner to receive a scoped share.
      </p>
      <p>
        Sign in with MFA to view a report. Access is checked on every request.
      </p>
      <Link to="/">Return to workspaces</Link>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          read.mutate(e.currentTarget);
        }}
      >
        <Field label="Share reference">
          <input
            name="grant_id"
            required
            defaultValue={params.get("grant") || ""}
          />
        </Field>
        <Field label="Report reference">
          <input
            name="report_id"
            required
            defaultValue={params.get("report") || ""}
          />
        </Field>
        <Button busy={read.isPending}>Open shared report</Button>
      </form>
      <ErrorNotice error={read.error} />
      {report && (
        <section>
          <h2>Saved workpaper</h2>
          <p>
            As of {report.as_of} · {report.currency} · {report.basis}
          </p>
          <p>
            Report {report.report_id}. This snapshot does not grant access to
            linked documents or other workspace records.
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              const url = URL.createObjectURL(
                new Blob([JSON.stringify(report, null, 2)], {
                  type: "application/json",
                }),
              );
              const link = document.createElement("a");
              link.href = url;
              link.download = "ledgerpilot-audit-workpaper.json";
              link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            }}
          >
            Download workpaper
          </Button>
        </section>
      )}
    </main>
  );
}
