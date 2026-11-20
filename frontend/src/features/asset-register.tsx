import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, result } from "../api/runtime";
import { Button, Field, ErrorNotice, Loading } from "../components/ui";
import { useBusiness } from "./context";

export function AssetRegister() {
  const b = useBusiness(),
    path = { business_id: b.id };
  const [selected, select] = useState(
      new URLSearchParams(window.location.search).get("asset") ?? "",
    ),
    [after, page] = useState<string>();
  const rows = useQuery({
    queryKey: ["asset-register", b.id, after],
    queryFn: () =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounting/assets", {
          params: { path, query: { after } },
        }),
      ),
  });
  const record = rows.data?.find((r) => r.id === selected);
  const schedule = useQuery({
    queryKey: ["asset-schedule", b.id, selected, record?.version],
    enabled: !!selected,
    queryFn: () =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/accounting/assets/{asset_id}/schedule",
          { params: { path: { ...path, asset_id: selected } } },
        ),
      ),
  });
  const journals = useQuery({
    queryKey: ["asset-journals", b.id, selected],
    enabled: !!selected,
    queryFn: () =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/accounting/assets/{asset_id}/journals",
          { params: { path: { ...path, asset_id: selected } } },
        ),
      ),
  });
  const save = useMutation({
    mutationFn: async (form: FormData) => {
      const v = (k: string) => String(form.get(k) || "");
      if (record)
        return result(
          api.PATCH(
            "/api/v1/businesses/{business_id}/accounting/assets/{asset_id}",
            {
              params: { path: { ...path, asset_id: selected } },
              body: {
                name: v("name"),
                expected_version: Number(record.version),
                disposed_on: v("disposal") || null,
              },
            },
          ),
        );
      return result(
        api.POST("/api/v1/businesses/{business_id}/accounting/assets", {
          params: { path },
          body: {
            name: v("name"),
            acquired: v("acquired"),
            cost: v("cost"),
            residual: v("residual"),
            months: Number(v("months")),
          },
        }),
      );
    },
    onSuccess: () => void rows.refetch(),
  });
  return (
    <details className="panel">
      <summary>Fixed asset register</summary>
      <p>
        Straight-line depreciation starts in the acquisition month. Installments
        fall at month end; no installment is due after disposal.
      </p>
      {rows.isPending && <Loading />}
      <ErrorNotice
        error={rows.error || save.error || schedule.error || journals.error}
      />
      <Field label="Registered asset">
        <select
          value={selected}
          onChange={(e) => {
            select(e.target.value);
            save.reset();
          }}
        >
          <option value="">Create an asset</option>
          {rows.data?.map((r) => (
            <option key={String(r.id)} value={String(r.id)}>
              {String(r.name)}
            </option>
          ))}
        </select>
      </Field>
      {rows.data?.length === 100 && (
        <Button
          onClick={() => {
            const last = rows.data?.at(-1);
            if (last) page(String(last.id));
            select("");
          }}
        >
          Next assets
        </Button>
      )}
      {after && (
        <Button
          onClick={() => {
            page(undefined);
            select("");
          }}
        >
          First assets
        </Button>
      )}
      <form
        key={selected}
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(new FormData(e.currentTarget));
        }}
      >
        <Field label="Asset name">
          <input
            name="name"
            required
            maxLength={160}
            defaultValue={String(record?.name ?? "")}
          />
        </Field>
        {record ? (
          <Field label="Disposal date (optional)">
            <input
              name="disposal"
              type="date"
              min={String(record.acquired)}
              defaultValue={String(record.disposed_on ?? "")}
            />
          </Field>
        ) : (
          <>
            {["acquired", "cost", "residual", "months"].map((k) => (
              <Field
                key={k}
                label={
                  {
                    acquired: "Acquisition date",
                    cost: "Acquisition cost",
                    residual: "Residual value",
                    months: "Useful life in months",
                  }[k]!
                }
              >
                <input
                  required
                  name={k}
                  type={k === "acquired" ? "date" : "number"}
                  step={k === "months" ? 1 : "any"}
                  min={k === "months" ? 1 : 0}
                />
              </Field>
            ))}
          </>
        )}
        <Button busy={save.isPending} disabled={!!record?.disposed_on}>
          {record ? "Save asset changes" : "Create asset"}
        </Button>
      </form>
      {save.isSuccess && <p role="status">Asset saved.</p>}
      {record && (
        <>
          <p>Asset reference: {selected}</p>
          <h4>Scheduled installments</h4>
          <ol>
            {schedule.data?.map((i) => (
              <li key={i.date}>
                {i.date}: {i.amount} {b.currency}
              </li>
            ))}
          </ol>
          <h4>Linked acquisition, depreciation and disposal journals</h4>
          {journals.data?.length === 0 && <p>No posted installments.</p>}
          {journals.data?.map((j) => (
            <a key={j.id} href={`/ledger?journal=${j.id}`}>
              {j.date} · {j.memo}
            </a>
          ))}
        </>
      )}
    </details>
  );
}
