import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, result, keys, queryClient } from "../api/runtime";
import {
  Button,
  Field,
  PageHeading,
  Table,
  Empty,
  ErrorNotice,
  Loading,
  NextPage,
  Modal,
  money,
  Badge,
} from "../components/ui";
import { today, useBusiness } from "./context";
export function Ledger() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [start, setStart] = useState(today(b.timezone).slice(0, 7) + "-01");
  const [end, setEnd] = useState(today(b.timezone));
  const [account, setAccount] = useState("");
  const [cursor, setCursor] = useState<string>();
  const [journal, setJournal] = useState<string>();
  const [reversal, setReversal] = useState<{
    accounting_date: string;
    memo: string;
    idempotency_key: string;
    expected_version: number;
  }>();
  const prepareReversal = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const v = new FormData(form),
        current = await result(
          api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
        );
      setReversal({
        accounting_date: String(v.get("date")),
        memo: String(v.get("memo")),
        expected_version: current.version,
        idempotency_key: crypto.randomUUID(),
      });
    },
  });
  const reverse = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/journals/{journal_id}/reversal",
          {
            params: { path: { ...path, journal_id: journal! } },
            body: reversal!,
          },
        ),
      ),
    onSuccess: () => {
      setReversal(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  const accounts = useQuery({
    queryKey: [...keys.business(b.id), "accounts"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const ledger = useQuery({
    queryKey: [...keys.business(b.id), "ledger", start, end, account, cursor],
    enabled: start <= end,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/ledger", {
          signal,
          params: {
            path,
            query: {
              start,
              end,
              account_id: account || undefined,
              cursor,
              limit: 50,
            },
          },
        }),
      ),
  });
  const detail = useQuery({
    queryKey: [...keys.business(b.id), "journal", journal],
    enabled: !!journal,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/journals/{journal_id}", {
          signal,
          params: { path: { ...path, journal_id: journal! } },
        }),
      ),
  });
  const download = useMutation({
    mutationFn: async (kind: "journal" | "evidence") => {
      const data = await result(
        kind === "journal"
          ? api.GET("/api/v1/businesses/{business_id}/exports/journals.csv", {
              params: { path, query: { start, end } },
              parseAs: "blob",
            })
          : api.GET(
              "/api/v1/businesses/{business_id}/exports/evidence-pack.zip",
              { params: { path, query: { start, end } }, parseAs: "blob" },
            ),
      );
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `ledgerpilot-${kind}-${start}-${end}.${kind === "journal" ? "csv" : "zip"}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
  const name = (id: string) =>
    accounts.data?.items.find((a) => a.id === id)?.name || id;
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="GENERAL LEDGER"
        title="General ledger"
        description="Follow posted entries back to their journal, approval and source."
        actions={
          <>
            <Button
              variant="secondary"
              busy={download.isPending}
              onClick={() => download.mutate("journal")}
            >
              Export journals
            </Button>
            <Button
              busy={download.isPending}
              onClick={() => download.mutate("evidence")}
            >
              Export evidence pack
            </Button>
          </>
        }
      />
      <div className="filterbar">
        <Field label="From">
          <input
            type="date"
            value={start}
            onChange={(e) => {
              setStart(e.target.value);
              setCursor(undefined);
            }}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            min={start}
            value={end}
            onChange={(e) => {
              setEnd(e.target.value);
              setCursor(undefined);
            }}
          />
        </Field>
        <Field label="Account">
          <select
            value={account}
            onChange={(e) => {
              setAccount(e.target.value);
              setCursor(undefined);
            }}
          >
            <option value="">All accounts</option>
            {accounts.data?.items.map((a) => (
              <option value={a.id} key={a.id}>
                {a.code} · {a.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ErrorNotice error={ledger.error || accounts.error || download.error} />
      <section className="panel">
        {ledger.isPending ? (
          <Loading />
        ) : ledger.data?.items.length ? (
          <>
            <Table
              caption="Posted ledger entries"
              rows={ledger.data.items}
              rowKey={(r) => r.id}
              columns={[
                { label: "Date", render: (r) => r.date },
                { label: "Account", render: (r) => name(r.account_id) },
                { label: "Debit", render: (r) => money(r.debit, b.currency) },
                { label: "Credit", render: (r) => money(r.credit, b.currency) },
                {
                  label: "Evidence trail",
                  render: (r) => (
                    <Button
                      variant="ghost"
                      onClick={() => setJournal(r.journal_id)}
                    >
                      View journal
                    </Button>
                  ),
                },
              ]}
            />
            <NextPage
              cursor={ledger.data.next_cursor}
              onNext={() => setCursor(ledger.data?.next_cursor || undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        ) : (
          <Empty title="No posted entries in this period.">
            Choose another date range or post a reviewed transaction.
          </Empty>
        )}
      </section>
      <Modal
        open={!!journal}
        onClose={() => setJournal(undefined)}
        title="Journal & source trail"
        description="Posted journal entries are immutable. Corrections use linked reversals."
      >
        <ErrorNotice error={detail.error} />
        {detail.isPending ? (
          <Loading />
        ) : (
          detail.data && (
            <>
              <Badge>{detail.data.status}</Badge>
              <h3>{detail.data.memo}</h3>
              <dl className="review-summary">
                <dt>Date</dt>
                <dd>{detail.data.accounting_date}</dd>
                <dt>Journal reference</dt>
                <dd>{detail.data.id}</dd>
                <dt>Source</dt>
                <dd>
                  {detail.data.source_kind} ·{" "}
                  {detail.data.source_id || "Manual entry"}
                </dd>
                <dt>Approved by</dt>
                <dd>{detail.data.approved_by || "Not approved"}</dd>
                <dt>Reversal</dt>
                <dd>
                  {detail.data.reversed_by || detail.data.reversal_of || "None"}
                </dd>
              </dl>
              <ErrorNotice error={prepareReversal.error || reverse.error} />
              {detail.data.status === "posted" &&
                !detail.data.reversed_by &&
                !detail.data.reversal_of && (
                  <details>
                    <summary>Correct with a linked reversal</summary>
                    {reversal ? (
                      <>
                        <p>
                          Reverse on {reversal.accounting_date}: {reversal.memo}
                        </p>
                        <Button
                          variant="secondary"
                          disabled={reverse.isPending}
                          onClick={() => setReversal(undefined)}
                        >
                          Cancel
                        </Button>
                        <Button
                          permission="post"
                          busy={reverse.isPending}
                          onClick={() => reverse.mutate()}
                        >
                          Approve linked reversal
                        </Button>
                      </>
                    ) : (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          prepareReversal.mutate(e.currentTarget);
                        }}
                      >
                        <Field label="Reversal date">
                          <input
                            name="date"
                            type="date"
                            min={detail.data.accounting_date}
                            required
                            defaultValue={today(b.timezone)}
                          />
                        </Field>
                        <Field label="Reason">
                          <textarea name="memo" required maxLength={2000} />
                        </Field>
                        <Button busy={prepareReversal.isPending}>
                          Review reversal
                        </Button>
                      </form>
                    )}
                  </details>
                )}
              <Table
                caption="Journal lines"
                rows={detail.data.lines}
                rowKey={(l) => String(l.position)}
                columns={[
                  { label: "Line", render: (l) => l.position },
                  { label: "Account", render: (l) => name(l.account_id) },
                  { label: "Debit", render: (l) => money(l.debit, b.currency) },
                  {
                    label: "Credit",
                    render: (l) => money(l.credit, b.currency),
                  },
                ]}
              />
            </>
          )
        )}
      </Modal>
    </>
  );
}
