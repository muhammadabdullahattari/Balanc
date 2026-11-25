import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, result } from "../api/runtime";
import { Button, ErrorNotice, Field, Loading } from "../components/ui";

const SORT_LABELS: Record<
  "exceptions" | "status" | "severity" | "assignee",
  string
> = {
  exceptions: "Open exception count (highest first)",
  status: "Exception review status",
  severity: "Exception severity",
  assignee: "Assigned accountant",
};

const STATUS_LABELS: Record<"all" | "clear" | "open", string> = {
  all: "All workspaces",
  clear: "Clear (0 open exceptions)",
  open: "Workspaces with open exceptions",
};

export function PortfolioTriage() {
  const [sort, setSort] = useState<
    "exceptions" | "status" | "severity" | "assignee"
  >("exceptions");
  const [status, setStatus] = useState<"all" | "clear" | "open">("all");
  const [offset, page] = useState(0),
    [selected, select] = useState("");
  const rows = useQuery({
    queryKey: ["portfolio", sort, status, offset],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/portfolio", {
          signal,
          params: { query: { sort, status, offset, limit: 25 } },
        }),
      ),
  });
  return (
    <section className="panel portfolio-triage-panel">
      <h2>Portfolio exceptions</h2>
      <p className="muted">
        Filter workspaces across your accounting practice and click any business
        below to inspect or assign open reconciliation and close exceptions.
      </p>
      <div className="form-grid">
        <Field label="Sort businesses">
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as typeof sort);
              page(0);
            }}
          >
            {(["exceptions", "status", "severity", "assignee"] as const).map(
              (x) => (
                <option key={x} value={x}>
                  {SORT_LABELS[x]}
                </option>
              ),
            )}
          </select>
        </Field>
        <Field label="Exception status">
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as typeof status);
              page(0);
            }}
          >
            {(["all", "clear", "open"] as const).map((x) => (
              <option key={x} value={x}>
                {STATUS_LABELS[x]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {rows.isPending ? <Loading /> : <ErrorNotice error={rows.error} />}
      {rows.data?.length === 0 && (
        <p className="muted" style={{ marginTop: "12px" }}>
          No matching businesses.
        </p>
      )}
      {rows.data && rows.data.length > 0 && (
        <div className="portfolio-chip-grid">
          {rows.data.map((r) => (
            <Button
              key={r.business_id}
              variant="secondary"
              className={
                selected === r.business_id ? "portfolio-chip-active" : ""
              }
              onClick={() => select(r.business_id)}
            >
              {r.business_name}: {r.exceptions} exceptions
            </Button>
          ))}
        </div>
      )}
      <div className="p3-actions-row">
        <Button
          variant="secondary"
          disabled={!offset}
          onClick={() => page(offset - 25)}
        >
          Previous 25
        </Button>
        <Button
          variant="secondary"
          disabled={rows.data?.length !== 25}
          onClick={() => page(offset + 25)}
        >
          Next 25
        </Button>
      </div>
      {selected && (
        <div className="portfolio-assignments-box">
          <Assignments
            key={selected}
            id={selected}
            changed={() => void rows.refetch()}
          />
        </div>
      )}
    </section>
  );
}
function Assignments({ id, changed }: { id: string; changed: () => void }) {
  const [offset, page] = useState(0),
    [source, choose] = useState("");
  const [before, setBefore] = useState<number>();
  const path = { business_id: id };
  const items = useQuery({
    queryKey: ["exceptions", id, offset],
    queryFn: () =>
      result(
        api.GET("/api/v1/businesses/{business_id}/portfolio-exceptions", {
          params: { path, query: { offset } },
        }),
      ),
  });
  const members = useQuery({
    queryKey: ["exception-members", id],
    queryFn: () =>
      result(
        api.GET("/api/v1/businesses/{business_id}/memberships", {
          params: { path },
        }),
      ),
  });
  const item = items.data?.find((x) => x.source_id === source);
  const history = useQuery({
    queryKey: ["assign-history", id, source, before],
    enabled: !!item,
    queryFn: () =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/portfolio-exceptions/{kind}/{source_id}/assignment-history",
          {
            params: {
              path: { ...path, kind: item!.kind, source_id: source },
              query: { before },
            },
          },
        ),
      ),
  });
  const assign = useMutation({
    mutationFn: (assignee: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/portfolio-exceptions/{kind}/{source_id}/assignment",
          {
            params: { path: { ...path, kind: item!.kind, source_id: source } },
            body: {
              assignee: assignee || null,
              expected_assignee: item!.assignee,
            },
          },
        ),
      ),
    onSuccess: async () => {
      setBefore(undefined);
      await Promise.all([items.refetch(), history.refetch()]);
      changed();
    },
  });
  return (
    <section>
      <h3>Assign an exception</h3>
      <ErrorNotice
        error={items.error || members.error || history.error || assign.error}
      />
      <Field label="Exception">
        <select
          value={source}
          onChange={(e) => {
            choose(e.target.value);
            setBefore(undefined);
          }}
        >
          <option value="">Select an exception</option>
          {items.data?.map((x) => (
            <option key={x.source_id} value={x.source_id}>
              {x.kind}: {x.description}
            </option>
          ))}
        </select>
      </Field>
      <Button
        disabled={!offset}
        onClick={() => {
          page(offset - 50);
          choose("");
        }}
      >
        Previous exceptions
      </Button>
      <Button
        disabled={items.data?.length !== 50}
        onClick={() => {
          page(offset + 50);
          choose("");
        }}
      >
        More exceptions
      </Button>
      {item && (
        <>
          <Field label="Assigned accountant">
            <select
              disabled={assign.isPending || members.isPending}
              value={item.assignee || ""}
              onChange={(e) => assign.mutate(e.target.value)}
            >
              <option value="">Unassigned</option>
              {members.data?.items
                .filter((m) => m.active && m.role === "accountant")
                .map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.user_id}
                  </option>
                ))}
            </select>
          </Field>
          <h4>Assignment history</h4>
          {before !== undefined && (
            <Button onClick={() => setBefore(undefined)}>
              Latest assignments
            </Button>
          )}
          <Button
            disabled={history.isFetching || history.data?.length !== 100}
            onClick={() => {
              const last = history.data?.at(-1);
              if (last) setBefore(Number(last.event_id));
            }}
          >
            Earlier assignments
          </Button>
          {history.data?.map((h) => (
            <p key={String(h.event_id)}>
              {String(h.changed_at)} · {String(h.actor)}:{" "}
              {String(h.previous_user ?? "Unassigned")} →{" "}
              {String(h.assigned_user ?? "Unassigned")}
            </p>
          ))}
        </>
      )}
    </section>
  );
}
