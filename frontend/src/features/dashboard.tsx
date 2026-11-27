import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  ArrowRight,
  Wallet,
  FileCheck2,
  CircleHelp,
} from "lucide-react";
import { api, result, keys } from "../api/runtime";
import {
  ChartErrorBoundary,
  IncomeExpenseLineChart,
} from "../components/report-charts";
import {
  Badge,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  money,
  PageHeading,
} from "../components/ui";
import { today, useBusiness } from "./context";

export function Dashboard() {
  const b = useBusiness();
  const day = today(b.timezone);
  const [start, setStart] = useState(day.slice(0, 7) + "-01");
  const [end, setEnd] = useState(day);
  const [bank, setBank] = useState("");
  const accounts = useQuery({
    queryKey: [...keys.business(b.id), "accounts"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path: { business_id: b.id }, query: { limit: 200 } },
        }),
      ),
  });
  const dashboard = useQuery({
    queryKey: [...keys.business(b.id), "dashboard", start, end, bank],
    enabled: !!bank && !!start && !!end && start <= end,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/dashboard", {
          signal,
          params: {
            path: { business_id: b.id },
            query: {
              start,
              end,
              bank_ids: [bank],
              basis: b.basis === "cash" ? "cash" : "accrual",
            },
          },
        }),
      ),
  });
  const executiveSummary = useQuery({
    queryKey: [...keys.business(b.id), "dashboard-executive-summary", end],
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/dashboard/executive-summary",
          {
            signal,
            params: {
              path: { business_id: b.id },
              query: { as_of: end },
            },
          },
        ),
      ),
  });
  const d = dashboard.data;
  const max = d
    ? Math.max(
        1,
        ...d.trend.flatMap((t) => [
          Math.abs(Number(t.income)),
          Math.abs(Number(t.expense)),
        ]),
      )
    : 1;
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="THE BIG PICTURE"
        title="Financial overview"
        description={`Review cash, open balances and financial activity for ${b.name}.`}
        actions={
          <Link className="button primary" to="/sales">
            Create an invoice <ArrowUpRight size={16} />
          </Link>
        }
      />
      <div className="filterbar">
        <Field label="Cash account">
          <select value={bank} onChange={(e) => setBank(e.target.value)}>
            <option value="">Choose an account</option>
            {accounts.data?.items
              .filter((a) => a.kind === "asset" && a.active)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </Field>
        <Field label="From">
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            min={start}
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </Field>
        <Badge>
          {b.basis} basis · {b.currency}
        </Badge>
      </div>
      <ErrorNotice
        error={accounts.error || dashboard.error}
        retry={() => {
          if (accounts.error) void accounts.refetch();
          if (dashboard.error && bank && start && end && start <= end)
            void dashboard.refetch();
        }}
      />
      {!bank ? (
        <div className="dashboard-grid">
          <div className="panel account-empty">
            <Empty
              title="Your overview starts with a cash account."
              action={
                <Link to="/accounts" className="button secondary">
                  Manage accounts <ArrowRight size={16} />
                </Link>
              }
            >
              Choose an account above. Your balances will come directly from
              your books.
            </Empty>
          </div>
          <WorkspaceShortcuts />
        </div>
      ) : !start || !end || start > end ? (
        <p className="notice error" role="alert">
          Choose a valid reporting date range.
        </p>
      ) : dashboard.isPending ? (
        <Loading />
      ) : (
        d && (
          <>
            <div className="metric-grid">
              {[
                {
                  label: "Cash balance",
                  value: d.cash_balance,
                  note: "Selected cash account",
                  icon: <Wallet />,
                },
                {
                  label: "Customers owe you",
                  value: d.receivables.total,
                  note: "Open receivables",
                  icon: <ArrowUpRight />,
                },
                {
                  label: "You owe suppliers",
                  value: d.payables.total,
                  note: "Open payables",
                  icon: <FileCheck2 />,
                },
                {
                  label: "Net profit",
                  value: d.net_profit,
                  note: "For this reporting period",
                  icon: <ArrowUpRight />,
                },
              ].map((m, i) => (
                <article
                  className={`metric ${i === 0 ? "featured" : ""}`}
                  key={m.label}
                >
                  <div>
                    <span>{m.label}</span>
                    {m.icon}
                  </div>
                  <strong>{money(m.value, d.currency)}</strong>
                  <small>{m.note}</small>
                </article>
              ))}
            </div>
            <div className="dashboard-grid">
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>Income and expenses</h2>
                    <p className="muted">
                      Income and expenses across your reporting period.
                    </p>
                  </div>
                  <Badge>Monthly</Badge>
                </div>
                {d.trend.length ? (
                  <>
                    <div
                      className="chart"
                      role="img"
                      tabIndex={0}
                      aria-label="Monthly income and expense comparison. Exact figures are in the table below."
                    >
                      {d.trend.map((t) => (
                        <div className="chart-month" key={t.start_date}>
                          <div className="bars">
                            <span
                              className="income-bar"
                              style={{
                                height: `${(Math.max(0, Number(t.income)) / max) * 100}%`,
                              }}
                            />
                            <span
                              className="expense-bar"
                              style={{
                                height: `${(Math.max(0, Number(t.expense)) / max) * 100}%`,
                              }}
                            />
                          </div>
                          <small>
                            {new Date(
                              t.start_date + "T12:00:00",
                            ).toLocaleDateString("en", { month: "short" })}
                          </small>
                        </div>
                      ))}
                    </div>
                    <div className="legend">
                      <span>
                        <i />
                        Income
                      </span>
                      <span>
                        <i />
                        Expenses
                      </span>
                    </div>
                    {d.trend.length > 1 && (
                      <ChartErrorBoundary>
                        <IncomeExpenseLineChart
                          title="Net balance trajectory"
                          currency={d.currency}
                          series={d.trend.map((t) => ({
                            period_label: t.start_date.slice(0, 7),
                            income: t.income,
                            expense: t.expense,
                            net_profit: String(
                              (Number(t.income) - Number(t.expense)).toFixed(2),
                            ),
                          }))}
                        />
                      </ChartErrorBoundary>
                    )}
                    <details className="chart-data">
                      <summary>View exact figures</summary>
                      <div
                        className="table-scroll"
                        role="region"
                        aria-label="Monthly income and expenses"
                        tabIndex={0}
                      >
                        <table>
                          <thead>
                            <tr>
                              <th>Month</th>
                              <th>Income</th>
                              <th>Expenses</th>
                            </tr>
                          </thead>
                          <tbody>
                            {d.trend.map((t) => (
                              <tr key={t.start_date}>
                                <td>{t.start_date}</td>
                                <td>{money(t.income, d.currency)}</td>
                                <td>{money(t.expense, d.currency)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  </>
                ) : (
                  <Empty title="Your next chapter starts here.">
                    Posted activity will appear in this chart.
                  </Empty>
                )}
              </section>
              <WorkspaceShortcuts />
            </div>
            {(() => {
              const exec = executiveSummary.data as
                | {
                    pending_decisions?: Array<{
                      key: string;
                      label: string;
                      count: number;
                      route: string;
                    }>;
                    due_soon_bills?: Array<{
                      id: string;
                      number: string;
                      due_date: string;
                      amount_due: string;
                    }>;
                    forecast_summary?: {
                      available?: boolean;
                      first_shortfall_week?: string | null;
                      suggestions?: string[];
                      weeks?: Array<{
                        week: number;
                        end_date: string;
                        p10_cash: string;
                        p50_cash: string;
                        p90_cash: string;
                      }>;
                    };
                  }
                | undefined;
              if (
                !exec ||
                (!Array.isArray(exec.pending_decisions) &&
                  !exec.forecast_summary?.available)
              ) {
                return null;
              }
              const decisions = exec.pending_decisions || [];
              const fc = exec.forecast_summary;
              const lastWk = fc?.weeks?.[fc.weeks.length - 1];
              return (
                <div
                  className="dashboard-grid"
                  style={{ marginTop: "1.25rem" }}
                >
                  <section className="panel settings-section">
                    <h2>Needs your decision</h2>
                    {decisions.length === 0 ? (
                      <p className="muted">
                        Everything is caught up — no flagged items are waiting
                        for human review right now.
                      </p>
                    ) : (
                      <ul>
                        {decisions.map((item) => (
                          <li
                            key={item.key}
                            style={{ marginBottom: "0.45rem" }}
                          >
                            <Link to={item.route}>
                              <strong>{item.count}</strong> · {item.label}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                  {fc?.available && (
                    <section className="panel settings-section">
                      <h2>13-week cash outlook & suggestions</h2>
                      {lastWk && (
                        <p className="muted">
                          Week {lastWk.week} projected range (estimated from
                          payment history):{" "}
                          <strong>{money(lastWk.p10_cash, d.currency)}</strong>{" "}
                          to{" "}
                          <strong>{money(lastWk.p90_cash, d.currency)}</strong>{" "}
                          (median {money(lastWk.p50_cash, d.currency)}).
                        </p>
                      )}
                      {Array.isArray(fc.suggestions) &&
                        fc.suggestions.length > 0 && (
                          <ul>
                            {fc.suggestions.map((s, idx) => (
                              <li key={idx}>{s}</li>
                            ))}
                          </ul>
                        )}
                    </section>
                  )}
                </div>
              );
            })()}
            <p className="report-footnote">
              Loaded{" "}
              {new Date(dashboard.dataUpdatedAt).toLocaleString("en", {
                timeZone: b.timezone,
              })}{" "}
              · As of {d.as_of} · {d.timezone} · {d.basis} basis · Report{" "}
              {d.report_id}
            </p>
          </>
        )
      )}
    </>
  );
}

function WorkspaceShortcuts() {
  return (
    <section className="panel focus-panel">
      <span className="eyebrow">WORKSPACE SHORTCUTS</span>
      <h2>Keep your books moving</h2>
      <p className="muted">
        Review bank activity, collect supporting documents, and keep your close
        moving.
      </p>
      <Link to="/banking" className="task-link">
        <span>
          Review bank transactions
          <small>Match or categorize with confidence</small>
        </span>
        <ArrowUpRight />
      </Link>
      <Link to="/evidence" className="task-link">
        <span>
          Organize your evidence
          <small>Receipts and invoices, together</small>
        </span>
        <ArrowUpRight />
      </Link>
      <Link to="/close" className="task-link">
        <span>
          Prepare your month-end
          <small>Work through your close checklist</small>
        </span>
        <ArrowUpRight />
      </Link>
      <div className="assurance">
        <CircleHelp size={17} />
        <span>
          Suggestions stay in review until an authorized person approves.
        </span>
      </div>
    </section>
  );
}
