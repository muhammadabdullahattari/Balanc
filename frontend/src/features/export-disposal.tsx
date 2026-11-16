import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, keys, result } from "../api/runtime";
import { Button, ErrorNotice, Table } from "../components/ui";
import { useBusiness } from "./context";

export function ExportDisposal() {
  const business = useBusiness();
  const path = { business_id: business.id };
  const key = [...keys.business(business.id), "export-disposal"];
  const client = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const preview = useQuery({
    queryKey: key,
    enabled: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/retention/disposal/exports", {
          params: { path },
          signal,
        }),
      ),
  });
  const dispose = useMutation({
    mutationFn: (ids: string[]) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/retention/disposal/exports",
          {
            params: { path },
            body: { ids },
          },
        ),
      ),
    onSuccess: async () => {
      setSelected([]);
      setConfirmed(false);
      await client.invalidateQueries({ queryKey: key });
      await preview.refetch();
    },
  });
  const eligible = selected.filter((id) =>
    preview.data?.items.some(
      (item) => item.id === id && item.decision === "eligible",
    ),
  );
  const enabled =
    preview.data?.execution_enabled && !preview.isError && !preview.isFetching;
  return (
    <section aria-label="Managed export disposal">
      <h3>Managed export disposal</h3>
      <p>
        Preview the server copies of exports you created. Active legal holds
        block deletion.
      </p>
      <Button
        variant="secondary"
        disabled={preview.isFetching || dispose.isPending}
        onClick={() => {
          setSelected([]);
          setConfirmed(false);
          dispose.reset();
          void preview.refetch();
        }}
      >
        {preview.isFetching ? "Checking exports…" : "Preview export disposal"}
      </Button>
      <ErrorNotice error={preview.error || dispose.error} />
      {dispose.data && (
        <p role="status">
          Deleted {dispose.data.deleted.length} managed export copies.
        </p>
      )}
      {preview.data && (
        <>
          {!preview.data.execution_enabled && (
            <p>Deletion is disabled until an approved policy is configured.</p>
          )}
          {preview.data.truncated && (
            <p>
              Showing the first 100 exports. Preview again after processing
              eligible copies.
            </p>
          )}
          <Table
            caption="Managed export copies"
            rows={preview.data.items}
            rowKey={(row) => row.id}
            columns={[
              { label: "Export", render: (row) => row.id },
              {
                label: "Expires",
                render: (row) => new Date(row.expires_at).toLocaleString(),
              },
              { label: "Decision", render: (row) => row.decision },
              {
                label: "Select",
                render: (row) =>
                  row.decision === "eligible" && (
                    <input
                      type="checkbox"
                      aria-label={`Select export ${row.id}`}
                      checked={selected.includes(row.id)}
                      disabled={!enabled || dispose.isPending}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setConfirmed(false);
                        setSelected((values) =>
                          checked
                            ? [...values, row.id]
                            : values.filter((id) => id !== row.id),
                        );
                      }}
                    />
                  ),
              },
            ]}
          />
          {eligible.length > 0 && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (enabled && confirmed && !dispose.isPending)
                  dispose.mutate(eligible);
              }}
            >
              <label>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) =>
                    setConfirmed(event.currentTarget.checked)
                  }
                />
                Confirm permanent removal of {eligible.length} selected managed
                export copies
              </label>
              <Button disabled={!enabled || !confirmed || dispose.isPending}>
                {dispose.isPending ? "Deleting…" : "Delete selected exports"}
              </Button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
