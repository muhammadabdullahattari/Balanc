import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, result, keys, queryClient } from "../api/runtime";
import {
  PageHeading,
  Field,
  Button,
  Table,
  Empty,
  ErrorNotice,
  Loading,
  NextPage,
  Badge,
} from "../components/ui";
import { useBusiness } from "./context";
export function Activity() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [cursor, setCursor] = useState<string>();
  const list = useQuery({
    queryKey: [...keys.business(b.id), "notifications", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/notifications", {
          signal,
          params: { path, query: { cursor, limit: 50 } },
        }),
      ),
  });
  const preferences = useQuery({
    queryKey: [...keys.business(b.id), "preferences"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/notification-preferences", {
          signal,
          params: { path },
        }),
      ),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };
  const read = useMutation({
    mutationFn: (id: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/notifications/{notification_id}/read",
          { params: { path: { ...path, notification_id: id } } },
        ),
      ),
    onSuccess: refresh,
  });
  const save = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.PATCH("/api/v1/businesses/{business_id}/notification-preferences", {
          params: { path },
          body: {
            expected_version: preferences.data!.version,
            reminders: v.get("reminders") === "on",
            marketing: v.get("marketing") === "on",
            quiet_start: Number(v.get("start")),
            quiet_end: Number(v.get("end")),
          },
        }),
      );
    },
    onSuccess: refresh,
  });
  return (
    <>
      <PageHeading
        eyebrow="ACTIVITY & PREFERENCES"
        title="Activity and notifications"
        description="Your business notifications and delivery preferences, together."
      />
      <ErrorNotice
        error={list.error || preferences.error || read.error || save.error}
      />
      <section className="panel">
        {list.isPending ? (
          <Loading />
        ) : list.data?.items.length ? (
          <>
            <Table
              caption="Business notifications"
              rows={list.data.items}
              rowKey={(n) => n.id}
              columns={[
                {
                  label: "Activity",
                  render: (n) => n.kind.replaceAll("_", " "),
                },
                {
                  label: "Created",
                  render: (n) =>
                    new Date(n.created_at).toLocaleString("en", {
                      timeZone: b.timezone,
                    }),
                },
                {
                  label: "Status",
                  render: (n) => <Badge>{n.read ? "Read" : "Unread"}</Badge>,
                },
                {
                  label: "Action",
                  render: (n) => (
                    <Button
                      variant="ghost"
                      disabled={n.read}
                      busy={read.isPending}
                      onClick={() => read.mutate(n.id)}
                    >
                      Mark read
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
        ) : (
          <Empty title="You’re all caught up.">
            New notifications will appear here.
          </Empty>
        )}
      </section>
      {preferences.data && (
        <section className="panel settings-section">
          <h2>Notification preferences</h2>
          <form
            key={preferences.data.version}
            onSubmit={(e) => {
              e.preventDefault();
              save.mutate(e.currentTarget);
            }}
          >
            <label className="check">
              <input
                name="reminders"
                type="checkbox"
                defaultChecked={preferences.data.reminders}
              />
              Invoice reminders
            </label>
            <label className="check">
              <input
                name="marketing"
                type="checkbox"
                defaultChecked={preferences.data.marketing}
              />
              Product updates
            </label>
            <div className="form-grid">
              <Field label="Quiet hours start (0–23)">
                <input
                  type="number"
                  name="start"
                  min={0}
                  max={23}
                  required
                  defaultValue={preferences.data.quiet_start}
                />
              </Field>
              <Field label="Quiet hours end (0–23)">
                <input
                  type="number"
                  name="end"
                  min={0}
                  max={23}
                  required
                  defaultValue={preferences.data.quiet_end}
                />
              </Field>
            </div>
            <Button busy={save.isPending}>Save preferences</Button>
            {save.isSuccess && <p role="status">Preferences saved.</p>}
          </form>
        </section>
      )}
    </>
  );
}
export function Search() {
  const b = useBusiness();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState<string>();
  const search = useQuery({
    queryKey: [...keys.business(b.id), "search", q, cursor],
    enabled: !!q,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/search", {
          signal,
          params: {
            path: { business_id: b.id },
            query: { q, cursor, limit: 25 },
          },
        }),
      ),
  });
  return (
    <>
      <PageHeading
        eyebrow="SEARCH YOUR BOOKS"
        title="Search records"
        description="Search document numbers within your selected business."
      />
      <form
        className="filterbar"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(String(new FormData(e.currentTarget).get("q")).trim());
          setCursor(undefined);
        }}
      >
        <Field label="Document number">
          <input name="q" required maxLength={100} type="search" />
        </Field>
        <Button>Search</Button>
      </form>
      <ErrorNotice error={search.error} />
      <section className="panel">
        {!q ? (
          <Empty title="Start with a document number.">
            Searches stay within this business.
          </Empty>
        ) : search.isPending ? (
          <Loading />
        ) : search.data?.items.length ? (
          <>
            <Table
              caption="Document search results"
              rows={search.data.items}
              rowKey={(r) => r.id}
              columns={[
                { label: "Number", render: (r) => r.number },
                { label: "Type", render: (r) => r.kind },
                { label: "Record reference", render: (r) => r.id },
              ]}
            />
            <NextPage
              cursor={search.data.next_cursor}
              onNext={() => setCursor(search.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="No matching documents.">Try a different number.</Empty>
        )}
      </section>
    </>
  );
}
