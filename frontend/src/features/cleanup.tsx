import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, keys, result } from "../api/runtime";
import { Button, ErrorNotice, Field, Table, humanize } from "../components/ui";
import { useAccess, useBusiness } from "./context";
import type { components } from "../api/generated/schema";

type Policy = components["schemas"]["PolicyView"];
type Cleanup = components["schemas"]["RequestView"];
const locations = {
  document_payload: "local_evidence",
  export_payload: "database_exports",
  backup_copy: "operator_backup",
  external_copy: "external_storage",
} as const;

export function CleanupControls() {
  const business = useBusiness();
  const access = useAccess();
  const path = { business_id: business.id };
  const key = [...keys.business(business.id), "cleanup"];
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [policyOffset, setPolicyOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const policies = useQuery({
    queryKey: [...key, "policies", policyOffset],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/retention/cleanup/policies", {
          params: { path, query: { offset: policyOffset } },
          signal,
        }),
      ),
  });
  const requests = useQuery({
    queryKey: [...key, "requests", offset],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/retention/cleanup/requests", {
          params: { path, query: { offset } },
          signal,
        }),
      ),
  });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: key });
  };
  const createPolicy = useMutation({
    mutationFn: (data: components["schemas"]["PolicyInput"]) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/policies",
          { params: { path }, body: data },
        ),
      ),
    onSuccess: refresh,
  });
  const approvePolicy = useMutation({
    mutationFn: (id: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/policies/{policy_id}/approve",
          { params: { path: { ...path, policy_id: id } } },
        ),
      ),
    onSuccess: refresh,
  });
  const createRequest = useMutation({
    mutationFn: (data: components["schemas"]["RequestInput"]) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/requests",
          { params: { path }, body: data },
        ),
      ),
    onSuccess: async (data) => {
      setSelected(data.id);
      await refresh();
    },
  });
  return (
    <section aria-label="Retention cleanup management">
      <h3>Approved payload cleanup</h3>
      <p>
        Accounting history stays intact. Policies and cleanup requests need
        independent approval. No deletion runs until the server is explicitly
        enabled. Backup and external removal require operator confirmation.
      </p>
      <ErrorNotice
        error={
          policies.error ||
          requests.error ||
          createPolicy.error ||
          approvePolicy.error ||
          createRequest.error
        }
      />
      {(policies.isPending || requests.isPending) && (
        <p role="status">Loading cleanup controls…</p>
      )}
      <details>
        <summary>Create a retention policy</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const d = new FormData(e.currentTarget);
            const record_type = String(
              d.get("record_type"),
            ) as Policy["record_type"];
            createPolicy.mutate({
              record_type,
              location: locations[record_type],
              retention_days: Number(d.get("days")),
              approval_role: String(d.get("role")) as Policy["approval_role"],
              reference: String(d.get("reference")),
            });
          }}
        >
          <Field label="Record and storage type">
            <select name="record_type">
              <option value="document_payload">
                Original document file — private local storage
              </option>
              <option value="export_payload">
                Generated export — database
              </option>
              <option value="backup_copy">
                Backup copy — operator controlled
              </option>
              <option value="external_copy">
                External copy — provider controlled
              </option>
            </select>
          </Field>
          <Field
            label="Retention period in days"
            hint="Use the period from your approved business policy; there is no default."
          >
            <input name="days" type="number" min={1} max={36525} required />
          </Field>
          <Field label="Required approver role">
            <select name="role">
              <option value="owner">Owner</option>
              <option value="accountant">Accountant</option>
            </select>
          </Field>
          <Field label="Policy / jurisdiction reference">
            <input name="reference" required maxLength={500} />
          </Field>
          <Button busy={createPolicy.isPending}>
            Save policy for approval
          </Button>
          {createPolicy.isSuccess && (
            <p role="status">Policy saved. Independent approval is required.</p>
          )}
        </form>
      </details>
      <Table
        caption="Cleanup policies"
        rows={policies.data || []}
        rowKey={(r) => r.id}
        columns={[
          { label: "Policy", render: (r) => r.reference },
          { label: "Location", render: (r) => humanize(r.location) },
          { label: "Days", render: (r) => r.retention_days },
          {
            label: "Approval",
            render: (r) =>
              r.approved_at ? (
                `Approved by ${r.approved_by}`
              ) : (
                <Button
                  disabled={
                    access?.role !== r.approval_role || approvePolicy.isPending
                  }
                  onClick={() => approvePolicy.mutate(r.id)}
                >
                  Approve policy
                </Button>
              ),
          },
        ]}
      />
      <Button
        variant="secondary"
        disabled={policyOffset === 0}
        onClick={() => setPolicyOffset((v) => Math.max(0, v - 100))}
      >
        Previous policies
      </Button>
      <Button
        variant="secondary"
        disabled={policies.data?.length !== 100}
        onClick={() => setPolicyOffset((v) => v + 100)}
      >
        More policies
      </Button>
      <details>
        <summary>Request cleanup</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const d = new FormData(e.currentTarget);
            createRequest.mutate({
              policy_id: String(d.get("policy")),
              resource_id: String(d.get("resource")),
              record_date: String(d.get("date")),
              reference: String(d.get("reference")),
            });
          }}
        >
          <Field label="Policy">
            <select name="policy" required>
              <option value="">Select policy</option>
              {policies.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.reference} · {humanize(p.location)}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Record or copy ID"
            hint="For external copies, enter the record ID from your archive list and note its storage location below."
          >
            <input name="resource" required />
          </Field>
          <Field
            label="Verified record date"
            hint="The independent reviewer must check this against source evidence."
          >
            <input name="date" type="date" required />
          </Field>
          <Field label="Record / storage reference and date evidence">
            <input name="reference" required maxLength={500} />
          </Field>
          <Button busy={createRequest.isPending}>Create cleanup request</Button>
        </form>
      </details>
      <Table
        caption="Cleanup requests"
        rows={requests.data || []}
        rowKey={(r) => r.id}
        columns={[
          { label: "Record", render: (r) => r.resource_id },
          { label: "Status", render: (r) => humanize(r.status) },
          { label: "Attempts", render: (r) => r.attempts },
          { label: "Outcome", render: (r) => r.outcome || "Awaiting dry run" },
          {
            label: "Review",
            render: (r) => (
              <Button
                variant="secondary"
                onClick={() => {
                  setSelected(r.id);
                  setConfirmed(false);
                }}
              >
                Open cleanup
              </Button>
            ),
          },
        ]}
      />
      <Button
        variant="secondary"
        disabled={offset === 0}
        onClick={() => setOffset((v) => Math.max(0, v - 100))}
      >
        Previous requests
      </Button>
      <Button
        variant="secondary"
        disabled={requests.data?.length !== 100}
        onClick={() => setOffset((v) => v + 100)}
      >
        More requests
      </Button>
      {selected && (
        <CleanupReview
          key={selected}
          id={selected}
          item={requests.data?.find((r) => r.id === selected)}
          confirmed={confirmed}
          setConfirmed={setConfirmed}
        />
      )}
    </section>
  );
}

function CleanupReview({
  id,
  item,
  confirmed,
  setConfirmed,
}: {
  id: string;
  item?: Cleanup;
  confirmed: boolean;
  setConfirmed: (value: boolean) => void;
}) {
  const business = useBusiness();
  const client = useQueryClient();
  const access = useAccess();
  const path = { business_id: business.id, request_id: id };
  const key = [...keys.business(business.id), "cleanup"];
  const [eventOffset, setEventOffset] = useState(0);
  const events = useQuery({
    queryKey: [...key, "events", id, eventOffset],
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/retention/cleanup/requests/{request_id}/events",
          { params: { path, query: { offset: eventOffset } }, signal },
        ),
      ),
  });
  const refresh = async () => {
    setConfirmed(false);
    await client.invalidateQueries({ queryKey: key });
  };
  const preview = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/requests/{request_id}/preview",
          { params: { path } },
        ),
      ),
    onSuccess: refresh,
  });
  const approve = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/requests/{request_id}/approve",
          { params: { path } },
        ),
      ),
    onSuccess: refresh,
  });
  const execute = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/requests/{request_id}/execute",
          { params: { path } },
        ),
      ),
    onSuccess: refresh,
  });
  const confirm = useMutation({
    mutationFn: (evidence_reference: string) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/cleanup/requests/{request_id}/confirm",
          { params: { path }, body: { evidence_reference } },
        ),
      ),
    onSuccess: refresh,
  });
  const busy =
    preview.isPending ||
    approve.isPending ||
    execute.isPending ||
    confirm.isPending;
  return (
    <section aria-label="Cleanup review">
      <h4>Cleanup review</h4>
      <p>{item?.reference}</p>
      <p>
        Record date: {item?.record_date}. Requested by: {item?.created_by}.
        Approved by: {item?.approved_by || "Awaiting approval"}.
      </p>
      <ErrorNotice
        error={
          events.error ||
          preview.error ||
          approve.error ||
          execute.error ||
          confirm.error
        }
      />
      <Button
        disabled={busy}
        onClick={() => {
          setConfirmed(false);
          preview.mutate();
        }}
      >
        Run dry run
      </Button>
      {preview.data && (
        <div role="status">
          <p>{preview.data.action}</p>
          <p>Decision: {preview.data.reason}</p>
          {!preview.data.execution_enabled && (
            <p>Destructive execution is disabled on the server.</p>
          )}
        </div>
      )}
      <Button
        disabled={busy || !preview.data?.eligible || !!item?.approved_at}
        onClick={() => approve.mutate()}
      >
        Approve reviewed request
      </Button>
      {approve.isSuccess && <p role="status">Independent approval recorded.</p>}
      <label>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.currentTarget.checked)}
        />
        I understand managed payload deletion is permanent. External copies
        remain pending until confirmed.
      </label>
      <Button
        disabled={
          busy ||
          !confirmed ||
          !preview.data?.eligible ||
          !preview.data.execution_enabled ||
          !item?.approved_at
        }
        onClick={() => execute.mutate()}
      >
        Execute approved cleanup
      </Button>
      {execute.data && (
        <p role="status">
          {execute.data.status}: {execute.data.outcome}
        </p>
      )}
      {item?.status === "awaiting_confirmation" && access?.role === "owner" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            confirm.mutate(
              String(new FormData(e.currentTarget).get("reference")),
            );
          }}
        >
          <Field
            label="External deletion confirmation evidence"
            hint="Enter an operator ticket or provider receipt, only after removal is confirmed."
          >
            <input name="reference" required minLength={5} maxLength={500} />
          </Field>
          <Button busy={confirm.isPending}>
            Record administrator confirmation
          </Button>
        </form>
      )}
      {confirm.isSuccess && (
        <p role="status">
          Administrator confirmation recorded; this is not machine verification.
        </p>
      )}
      <Table
        caption="Cleanup audit history"
        rows={events.data || []}
        rowKey={(r) => r.id}
        columns={[
          {
            label: "When",
            render: (r) => new Date(r.created_at).toLocaleString(),
          },
          { label: "Who", render: (r) => r.actor_id },
          { label: "Action", render: (r) => r.action },
          { label: "Detail", render: (r) => r.detail.reason },
        ]}
      />
      <Button
        variant="secondary"
        disabled={eventOffset === 0}
        onClick={() => setEventOffset((v) => Math.max(0, v - 100))}
      >
        Previous events
      </Button>
      <Button
        variant="secondary"
        disabled={events.data?.length !== 100}
        onClick={() => setEventOffset((v) => v + 100)}
      >
        More events
      </Button>
    </section>
  );
}
