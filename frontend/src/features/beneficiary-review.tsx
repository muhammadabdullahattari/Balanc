import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, result, keys, queryClient } from "../api/runtime";
import {
  Button,
  Field,
  ErrorNotice,
  Table,
  NextPage,
  Badge,
} from "../components/ui";
import { useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function BeneficiaryReview({
  party,
}: {
  party: components["schemas"]["PartyRead"];
}) {
  const b = useBusiness(),
    path = { business_id: b.id };
  const [cursor, setCursor] = useState<string>();
  const [command, setCommand] =
    useState<components["schemas"]["ChangeRequest"]>();
  const changes = useQuery({
    queryKey: [...keys.business(b.id), "beneficiary-changes", cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/beneficiary-changes", {
          signal,
          params: { path, query: { limit: 50, cursor } },
        }),
      ),
  });
  const prepare = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      setCommand({
        idempotency_key: crypto.randomUUID(),
        expected_party_version: party.version,
        account_name: String(v.get("account_name")),
        bank_name: String(v.get("bank_name")),
        account_reference: String(v.get("account_reference")),
        reason: String(v.get("reason")),
      });
      return Promise.resolve();
    },
  });
  const submit = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/parties/{party_id}/beneficiary-changes",
          { params: { path: { ...path, party_id: party.id } }, body: command! },
        ),
      ),
    onSuccess: () => {
      setCommand(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <details>
      <summary>Supplier bank-detail change review</summary>
      <div className="notice">
        Bank-detail changes are held for independent verification. A proposal
        does not activate a beneficiary or execute a payment.
      </div>
      <ErrorNotice error={changes.error || prepare.error || submit.error} />
      {command ? (
        <>
          <p>
            Proposed bank: {command.bank_name}. Account holder:{" "}
            {command.account_name}.
          </p>
          <Button
            variant="secondary"
            disabled={submit.isPending}
            onClick={() => setCommand(undefined)}
          >
            Cancel proposal
          </Button>
          <Button busy={submit.isPending} onClick={() => submit.mutate()}>
            {submit.isError ? "Retry same proposal" : "Record held proposal"}
          </Button>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            prepare.mutate(e.currentTarget);
          }}
        >
          <Field label="Account holder">
            <input
              name="account_name"
              required
              maxLength={120}
              autoComplete="off"
            />
          </Field>
          <Field label="Bank name">
            <input name="bank_name" required maxLength={120} />
          </Field>
          <Field label="New account reference">
            <input
              name="account_reference"
              required
              minLength={4}
              maxLength={80}
              autoComplete="off"
            />
          </Field>
          <Field label="Change reason">
            <textarea name="reason" required maxLength={1000} />
          </Field>
          <Button busy={prepare.isPending}>Review proposed change</Button>
        </form>
      )}
      {submit.isSuccess && (
        <p role="status">Proposal recorded for independent verification.</p>
      )}
      {changes.data && (
        <>
          <h3>Business change alerts</h3>
          <Table
            caption="Held beneficiary change alerts"
            rows={changes.data.items}
            rowKey={(r) => r.id}
            columns={[
              { label: "Reference", render: (r) => r.masked_reference },
              {
                label: "Status",
                render: (r) => <Badge tone="warning">{r.status}</Badge>,
              },
              { label: "Reason", render: (r) => r.reason },
              {
                label: "Created",
                render: (r) =>
                  new Date(r.created_at).toLocaleString("en", {
                    timeZone: b.timezone,
                  }),
              },
            ]}
          />
          <NextPage
            cursor={changes.data.next_cursor}
            onNext={() => setCursor(changes.data?.next_cursor || undefined)}
            onReset={() => setCursor(undefined)}
          />
        </>
      )}
    </details>
  );
}
