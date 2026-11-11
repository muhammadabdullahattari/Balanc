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
  SectionTabs,
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
  const [scenario, setScenario] = useState("baseline");
  const stress = b.p2_insights_available && scenario === "stress";
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
    queryKey: [...keys.business(b.id), "forecast", end, bank, stress],
    enabled: tab === "forecast" && !!bank,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/reports/forecast", {
          signal,
          params: {
            path,
            query: {
              as_of: end,
              bank_ids: [bank],
              ...(stress && { receipt_percent: 75, payment_percent: 125 }),
            },
          },
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
        title="Financial reports"
        description="Review balances, cash forecasts and the entries behind each figure."
      />
      <SectionTabs
        id="reports"
        label="Financial reports"
        value={tab}
        onChange={setTab}
        items={[
          ["profit", "Profit & loss"],
          ["balance", "Balance sheet"],
          ["trial", "Trial balance"],
          ["receivables", "Receivables"],
          ["payables", "Payables"],
          ["forecast", "Cash forecast"],
          ["tax", "Tax support"],
        ]}
      />
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
        {tab === "forecast" && b.p2_insights_available && (
          <Field label="Cash scenario">
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
            >
              <option value="baseline">Due-date baseline</option>
              <option value="stress">
                Stress case · 75% receipts, 125% payments
              </option>
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
      <section
        className="panel report-panel"
        id="reports-panel"
        role="tabpanel"
        aria-labelledby={`reports-${tab}`}
        tabIndex={0}
      >
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
                {tab === "profit" && b.p2_insights_available && (
                  <ProfitComparison
                    key={financial.data.report_id}
                    reportId={financial.data.report_id}
                    start={financial.data.start_date}
                    end={financial.data.as_of}
                    basis={basis}
                  />
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
                  The forecast assumes payments follow their due dates.
                  Statistical confidence bands are unavailable.
                </div>
                {b.p2_insights_available && (
                  <p role="status" className="notice">
                    {(() => {
                      const first = forecast.data.weeks.find(
                        (week) => week.shortfall,
                      );
                      return first
                        ? `First projected shortfall in the week ending ${first.end_date}.`
                        : "No closing cash shortfall in the 13-week projection.";
                    })()}
                  </p>
                )}
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
                {b.p2_insights_available && (
                  <ForecastAnswers
                    key={forecast.data.report_id}
                    reportId={forecast.data.report_id}
                  />
                )}
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

function ForecastAnswers({ reportId }: { reportId: string }) {
  const business = useBusiness();
  const answer = useMutation({
    mutationFn: (
      metric: "forecast_first_shortfall" | "forecast_closing_cash",
    ) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/assistant/report-answer", {
          params: { path: { business_id: business.id } },
          body: { report_id: reportId, metric },
        }),
      ),
  });
  const data = answer.data;
  return (
    <div className="settings-section">
      <h3>Ask about this forecast</h3>
      <div className="actions">
        <Button
          type="button"
          variant="secondary"
          disabled={answer.isPending}
          onClick={() => answer.mutate("forecast_first_shortfall")}
        >
          When is the first projected shortfall?
        </Button>
        <Button
          type="button"
          variant="secondary"
          disabled={answer.isPending}
          onClick={() => answer.mutate("forecast_closing_cash")}
        >
          What is the projected closing cash?
        </Button>
      </div>
      <ErrorNotice error={answer.error} />
      {data && (
        <p role="status">
          {data.metric === "forecast_first_shortfall"
            ? data.value
              ? `The first projected shortfall is in the week ending ${data.value}.`
              : "This forecast shows no projected shortfall."
            : `Projected cash at the end of week 13 is ${money(String(data.value), business.currency)}.`}{" "}
          Source report {data.report_id}.
        </p>
      )}
    </div>
  );
}

function ProfitComparison({
  reportId,
  start,
  end,
  basis,
}: {
  reportId: string;
  start: string;
  end: string;
  basis: "cash" | "accrual";
}) {
  const b = useBusiness();
  const [requested, setRequested] = useState(false);
  const first = Date.parse(start);
  const last = Date.parse(end);
  const day = 86_400_000;
  const priorStart = first - (last - first + day);
  const dateOnly = (value: number) =>
    new Date(value).toISOString().slice(0, 10);
  const comparable =
    Number.isFinite(priorStart) && priorStart >= Date.parse("0001-01-02");
  const comparison = useQuery({
    queryKey: [...keys.business(b.id), "profit-variance", reportId],
    enabled: requested && comparable,
    queryFn: async ({ signal }) => {
      const path = { business_id: b.id };
      const previous = await result(
        api.GET("/api/v1/businesses/{business_id}/reports/profit-loss", {
          signal,
          params: {
            path,
            query: {
              start: dateOnly(priorStart),
              end: dateOnly(first - day),
              basis,
            },
          },
        }),
      );
      return result(
        api.GET("/api/v1/businesses/{business_id}/reports/profit-variance", {
          signal,
          params: {
            path,
            query: {
              current_report_id: reportId,
              previous_report_id: previous.report_id,
            },
          },
        }),
      );
    },
  });
  if (!comparable) return null;
  const change = comparison.data;
  return (
    <div className="settings-section">
      <Button
        variant="secondary"
        busy={comparison.isFetching}
        onClick={() => setRequested(true)}
      >
        Compare with the previous period
      </Button>
      <ErrorNotice
        error={comparison.error}
        retry={() => void comparison.refetch()}
      />
      {change && (
        <div className="notice" role="status">
          <strong>
            Net profit change {money(change.net_profit_change, change.currency)}
          </strong>
          <p>
            Income changed by {money(change.income_change, change.currency)} and
            expenses changed by {money(change.expense_change, change.currency)}{" "}
            compared with {dateOnly(priorStart)}
            {" through "}
            {dateOnly(first - day)}.
          </p>
          {change.drivers.length > 0 && (
            <>
              <p>Largest account contributions to the change</p>
              <ul>
                {change.drivers.map((row) => (
                  <li key={row.account_id}>
                    {row.name} · {money(row.contribution, change.currency)}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p>
            Source reports {change.current_report_id} and{" "}
            {change.previous_report_id}.
          </p>
        </div>
      )}
    </div>
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
