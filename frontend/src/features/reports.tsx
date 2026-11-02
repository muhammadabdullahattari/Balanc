import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, keys, result } from "../api/runtime";
import {
  Button,
  Badge,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  money,
  PageHeading,
  Table,
} from "../components/ui";
import { today, useBusiness } from "./context";

export function Reports() {
  const b = useBusiness();
  const day = today(b.timezone);
  const [tab, setTab] = useState("profit");
  const [start, setStart] = useState(day.slice(0, 4) + "-01-01");
  const [end, setEnd] = useState(day);
  const [bank, setBank] = useState("");
  const path = { business_id: b.id };
  const basis = b.basis === "cash" ? ("cash" as const) : ("accrual" as const);
  const financial = useQuery({
    queryKey: [...keys.business(b.id), "financial", tab, start, end],
    enabled: ["profit", "balance"].includes(tab) && start <= end,
    queryFn: ({ signal }) =>
      tab === "profit"
        ? result(
            api.GET("/api/v1/businesses/{business_id}/reports/profit-loss", {
              signal,
              params: { path, query: { start, end, basis } },
            }),
          )
        : result(
            api.GET("/api/v1/businesses/{business_id}/reports/balance-sheet", {
              signal,
              params: { path, query: { as_of: end, basis } },
            }),
          ),
  });
  const trial = useQuery({
    queryKey: [...keys.business(b.id), "trial", end],
    enabled: tab === "trial",
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/trial-balance", {
          signal,
          params: { path, query: { as_of: end } },
        }),
      ),
  });
  const aging = useQuery({
    queryKey: [...keys.business(b.id), "aging", tab, end],
    enabled: tab === "receivables" || tab === "payables",
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/reports/aging", {
          signal,
          params: {
            path,
            query: {
              as_of: end,
              kind: tab === "receivables" ? "invoice" : "bill",
            },
          },
        }),
      ),
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
  const forecast = useQuery({
    queryKey: [...keys.business(b.id), "forecast", end, bank],
    enabled: tab === "forecast" && !!bank,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/reports/forecast", {
          signal,
          params: { path, query: { as_of: end, bank_ids: [bank] } },
        }),
      ),
  });
  const explain = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/assistant/report-answer", {
          params: { path },
          body: { report_id: financial.data!.report_id, metric: "net_profit" },
        }),
      ),
  });
  const tax = useQuery({
    queryKey: [...keys.business(b.id), "tax", start, end],
    enabled: tab === "tax" && start <= end,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/reports/tax-support", {
          signal,
          params: { path, query: { start, end } },
        }),
      ),
  });
  const report =
    tab === "tax"
      ? tax
      : tab === "trial"
        ? trial
        : tab === "forecast"
          ? forecast
          : ["receivables", "payables"].includes(tab)
            ? aging
            : financial;
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="REPORTS & INSIGHTS"
        title="The story behind the numbers."
        description="Reliable totals, a clear reporting basis, and a trail back to your books."
      />
      <div className="tabs" role="tablist" aria-label="Financial reports">
        {[
          ["profit", "Profit & loss"],
          ["balance", "Balance sheet"],
          ["trial", "Trial balance"],
          ["receivables", "Receivables"],
          ["payables", "Payables"],
          ["forecast", "Cash forecast"],
          ["tax", "Tax support"],
        ].map(([key, label]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            key={key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="filterbar">
        {["profit", "tax"].includes(tab) && (
          <Field label="From">
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </Field>
        )}
        <Field label="As of / through">
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </Field>
        {tab === "forecast" && (
          <Field label="Cash account">
            <select value={bank} onChange={(e) => setBank(e.target.value)}>
              <option value="">Choose account</option>
              {accounts.data?.items
                .filter((a) => a.kind === "asset" && a.active)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
        <Badge>
          {b.currency} · {b.basis} basis
        </Badge>
      </div>
      <ErrorNotice
        error={report.error}
        retry={() => {
          void report.refetch();
        }}
      />
      <section className="panel report-panel">
        {tab === "forecast" && !bank ? (
          <Empty title="Choose the cash account to forecast.">
            Your forecast follows the due dates in your books and discloses its
            assumptions.
          </Empty>
        ) : report.isPending ? (
          <Loading />
        ) : (
          <>
            {["profit", "balance"].includes(tab) && financial.data && (
              <>
                <div className="panel-heading">
                  <h2>
                    {tab === "profit" ? "Profit & loss" : "Balance sheet"}
                  </h2>
                  <Badge>{end}</Badge>
                </div>
                <Table
                  caption="Financial report"
                  rows={financial.data.items}
                  rowKey={(r) => r.account_id}
                  columns={[
                    { label: "Code", render: (r) => r.code },
                    {
                      label: "Account",
                      render: (r) => <strong>{r.name}</strong>,
                    },
                    { label: "Classification", render: (r) => r.kind },
                    {
                      label: "Amount",
                      render: (r) => money(r.amount, b.currency),
                    },
                  ]}
                />
                {tab === "profit" && (
                  <div className="report-total">
                    <span>Net profit</span>
                    <strong>
                      {money(financial.data.net_profit, b.currency)}
                    </strong>
                  </div>
                )}
                {tab === "profit" && (
                  <div className="settings-section">
                    <Button
                      variant="secondary"
                      busy={explain.isPending}
                      onClick={() => explain.mutate()}
                    >
                      Explain this profit figure
                    </Button>
                    <ErrorNotice error={explain.error} />
                    {explain.data &&
                      explain.data.report_id === financial.data.report_id && (
                        <div role="status" className="notice">
                          <div>
                            Net profit: {String(explain.data.value)}{" "}
                            {explain.data.currency}.<br />
                            Source report: {explain.data.report_id}. Basis:{" "}
                            {explain.data.basis}.<br />
                            This is a cited report lookup; causal explanations
                            require additional evidence.
                          </div>
                        </div>
                      )}
                  </div>
                )}
                <p className="report-footnote">
                  {financial.data.timezone} · Report {financial.data.report_id}
                </p>
              </>
            )}
            {tab === "tax" && tax.data && (
              <>
                <div className="panel-heading">
                  <h2>Tax support workpaper</h2>
                  <Badge>
                    {tax.data.balanced ? "Tied out" : "Needs review"}
                  </Badge>
                </div>
                <Table
                  caption="Tax control workpaper"
                  rows={tax.data.items}
                  rowKey={(r) => r.account_id}
                  columns={[
                    { label: "Account", render: (r) => r.name },
                    {
                      label: "Opening",
                      render: (r) =>
                        money(r.opening_debit_minus_credit, b.currency),
                    },
                    {
                      label: "Closing",
                      render: (r) =>
                        money(r.closing_debit_minus_credit, b.currency),
                    },
                    {
                      label: "Source balance",
                      render: (r) => money(r.source_balance, b.currency),
                    },
                    {
                      label: "Difference",
                      render: (r) => money(r.difference, b.currency),
                    },
                  ]}
                />
                <p className="report-footnote">
                  {tax.data.policy} · Source report {tax.data.report_id}.
                  Accountant review is required for statutory treatment.
                </p>
              </>
            )}
            {tab === "trial" && trial.data && (
              <>
                <div className="panel-heading">
                  <h2>Trial balance</h2>
                  <Badge tone={trial.data.balanced ? "good" : "warning"}>
                    {trial.data.balanced ? "Balanced" : "Needs review"}
                  </Badge>
                </div>
                <Table
                  caption="Trial balance"
                  rows={trial.data.items}
                  rowKey={(r) => r.account_id}
                  columns={[
                    {
                      label: "Account",
                      render: (r) => `${r.code} · ${r.name}`,
                    },
                    {
                      label: "Debit",
                      render: (r) => money(r.debit, b.currency),
                    },
                    {
                      label: "Credit",
                      render: (r) => money(r.credit, b.currency),
                    },
                  ]}
                />
                <div className="report-total">
                  <span>Debit {money(trial.data.total_debit, b.currency)}</span>
                  <strong>
                    Credit {money(trial.data.total_credit, b.currency)}
                  </strong>
                </div>
              </>
            )}
            {["receivables", "payables"].includes(tab) && aging.data && (
              <>
                <div className="panel-heading">
                  <h2>
                    {tab === "receivables"
                      ? "Outstanding receivables"
                      : "Outstanding payables"}
                  </h2>
                  <strong>{money(aging.data.total, b.currency)}</strong>
                </div>
                <ReportRecords rows={aging.data.items} currency={b.currency} />
                <p className="report-footnote">
                  As of {aging.data.as_of} · Report {aging.data.report_id}
                </p>
              </>
            )}
            {tab === "forecast" && forecast.data && (
              <>
                <div className="panel-heading">
                  <h2>The next 13 weeks</h2>
                  <Badge tone="warning">{forecast.data.confidence}</Badge>
                </div>
                <div className="notice">
                  This is a due-date forecast, not a guaranteed outcome. No
                  calibrated confidence bands are available.
                </div>
                <Table
                  caption="13-week cash forecast"
                  rows={forecast.data.weeks}
                  rowKey={(r) => String(r.week)}
                  columns={[
                    { label: "Week of", render: (r) => r.start_date },
                    {
                      label: "Receipts",
                      render: (r) => money(r.receipts, b.currency),
                    },
                    {
                      label: "Payments",
                      render: (r) => money(r.payments, b.currency),
                    },
                    {
                      label: "Closing cash",
                      render: (r) => money(r.closing_cash, b.currency),
                    },
                    {
                      label: "Review",
                      render: (r) =>
                        r.shortfall ? (
                          <Badge tone="warning">Cash shortfall</Badge>
                        ) : (
                          "—"
                        ),
                    },
                  ]}
                />
                <details className="chart-data">
                  <summary>Forecast assumptions</summary>
                  <ul>
                    {forecast.data.assumptions.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </details>
                <p className="report-footnote">
                  Report {forecast.data.report_id}
                </p>
              </>
            )}
          </>
        )}
      </section>
    </>
  );
}

function ReportRecords({
  rows,
  currency,
}: {
  rows: object[];
  currency: string;
}) {
  if (!rows.length)
    return (
      <Empty title="Nothing outstanding.">
        There are no balances for this selection.
      </Empty>
    );
  const entries = rows.map((r) =>
    Object.entries(r).filter(([k]) => !k.endsWith("_id")),
  );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {entries[0].map(([key]) => (
              <th key={key}>{key.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((row, i) => (
            <tr key={i}>
              {row.map(([key, value]) => (
                <td key={key}>
                  {typeof value === "string" && /^-?\d+\.\d+$/.test(value)
                    ? money(value, currency)
                    : String(value ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
