import { AskBooks } from "./accountant-tools";
import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { api, keys, result } from "../api/runtime";
import {
  CashBalanceTrendChart,
  ChartErrorBoundary,
  GrafanaEmbedBanner,
  GrafanaIframePanel,
  type GrafanaStatusInfo,
  HorizontalBarBreakdown,
  IncomeExpenseLineChart,
  PeriodGroupedBarsChart,
  type SeriesPoint,
  VisualExportBar,
  VisualKpiRow,
} from "../components/report-charts";
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

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function collapseAccountSlices(
  items: Array<{ name: string; kind: string; amount: string }>,
  targetKind: string,
): Array<{ label: string; value: string; sub?: string }> {
  const matching = items
    .filter((i) => i.kind === targetKind && Math.abs(Number(i.amount)) > 0)
    .sort((a, b) => Math.abs(Number(b.amount)) - Math.abs(Number(a.amount)));
  const total = matching.reduce(
    (acc, r) => acc + Math.abs(Number(r.amount)),
    0,
  );
  if (!matching.length || total <= 0) return [];
  const head = matching.slice(0, 6);
  const rest = matching.slice(6);
  const slices = head.map((r) => ({
    label: r.name,
    value: String(Math.abs(Number(r.amount)).toFixed(2)),
    sub: `${((Math.abs(Number(r.amount)) / total) * 100).toFixed(1)}%`,
  }));
  if (rest.length > 0) {
    const restSum = rest.reduce(
      (acc, r) => acc + Math.abs(Number(r.amount)),
      0,
    );
    slices.push({
      label: "Other categories",
      value: String(restSum.toFixed(2)),
      sub: `${((restSum / total) * 100).toFixed(1)}%`,
    });
  }
  return slices;
}

export function Reports() {
  const b = useBusiness();
  const day = today(b.timezone);
  const [tab, setTab] = useState("profit");
  const [start, setStart] = useState(day.slice(0, 4) + "-01-01");
  const [end, setEnd] = useState(day);
  const [periodMode, setPeriodMode] = useState<"monthly" | "yearly" | "custom">(
    "monthly",
  );
  const [showLiveGrafana, setShowLiveGrafana] = useState(false);
  const [emailNotice, setEmailNotice] = useState("");
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
  const visualsQuery = useQuery({
    queryKey: [
      ...keys.business(b.id),
      "report-visuals",
      tab,
      start,
      end,
      basis,
      periodMode,
    ],
    enabled: ["profit", "balance"].includes(tab) && start <= end,
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/reports/visuals", {
          signal,
          params: {
            path,
            query: {
              start: tab === "balance" ? end.slice(0, 4) + "-01-01" : start,
              end,
              basis,
              period_mode: periodMode,
            },
          },
        }),
      ),
  });
  const downloadVisualMutation = useMutation({
    mutationFn: async (fmt: "pdf" | "html") => {
      const reportType =
        tab === "balance"
          ? ("balance-sheet" as const)
          : periodMode === "yearly"
            ? ("yearly-summary" as const)
            : ("monthly-summary" as const);
      const res = await api.GET(
        "/api/v1/businesses/{business_id}/reports/visual-export",
        {
          params: {
            path,
            query: {
              report_type: reportType,
              format: fmt,
              start,
              end,
              as_of: end,
              basis,
            },
          },
          parseAs: "blob",
        },
      );
      if (!res.response.ok || !res.data) {
        throw new Error("Could not export visual report.");
      }
      triggerBlobDownload(res.data as Blob, `${reportType}-${end}.${fmt}`);
    },
  });
  const emailVisualMutation = useMutation({
    mutationFn: (recipientEmail: string) => {
      const reportType =
        tab === "balance"
          ? ("balance-sheet" as const)
          : periodMode === "yearly"
            ? ("yearly-summary" as const)
            : ("monthly-summary" as const);
      return result(
        api.POST("/api/v1/businesses/{business_id}/reports/email-visual", {
          params: { path },
          body: {
            recipient_email: recipientEmail,
            report_type: reportType,
            start,
            end,
            as_of: end,
            basis,
            theme: "light",
          },
        }),
      );
    },
    onSuccess: (_data, recipientEmail) => {
      setEmailNotice(`Visual report sent to ${recipientEmail}.`);
    },
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
      <AskBooks key={b.id} />
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
        {["profit", "balance"].includes(tab) && (
          <Field label="Report cadence">
            <select
              value={periodMode}
              onChange={(e) => {
                const next = e.target.value as "monthly" | "yearly" | "custom";
                setPeriodMode(next);
                if (next === "monthly") {
                  setStart(end.slice(0, 7) + "-01");
                } else if (next === "yearly") {
                  setStart(end.slice(0, 4) + "-01-01");
                }
              }}
            >
              <option value="monthly">Monthly view</option>
              <option value="yearly">Yearly view</option>
              <option value="custom">Custom range</option>
            </select>
          </Field>
        )}
        {["profit", "tax"].includes(tab) && (
          <Field label="From">
            <input
              type="date"
              value={start}
              onChange={(e) => {
                setStart(e.target.value);
                if (tab === "profit") setPeriodMode("custom");
              }}
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
                <ChartErrorBoundary>
                  {(() => {
                    const rawVis = visualsQuery.data as
                      Record<string, unknown> | undefined;
                    const grafanaInfo =
                      rawVis &&
                      typeof rawVis.grafana === "object" &&
                      rawVis.grafana !== null
                        ? (rawVis.grafana as GrafanaStatusInfo)
                        : undefined;
                    const embedUrls = grafanaInfo?.embed_urls || {};
                    const reportTypeParam =
                      tab === "balance"
                        ? "balance-sheet"
                        : periodMode === "yearly"
                          ? "yearly-summary"
                          : "monthly-summary";
                    const baseExportUrl = `/api/v1/businesses/${b.id}/reports/visual-export?report_type=${reportTypeParam}&start=${start}&end=${end}&as_of=${end}&basis=${basis}`;

                    const items = Array.isArray(financial.data.items)
                      ? financial.data.items
                      : [];
                    const kpis =
                      rawVis &&
                      typeof rawVis.kpis === "object" &&
                      rawVis.kpis !== null
                        ? (rawVis.kpis as Record<string, string>)
                        : undefined;

                    if (tab === "profit") {
                      const fallbackInc = items
                        .filter((r) => r.kind === "income")
                        .reduce((acc, r) => acc + Number(r.amount || 0), 0);
                      const fallbackExp = items
                        .filter((r) => r.kind === "expense")
                        .reduce((acc, r) => acc + Number(r.amount || 0), 0);
                      const totalInc =
                        kpis?.total_income ?? String(fallbackInc.toFixed(2));
                      const totalExp =
                        kpis?.total_expense ?? String(fallbackExp.toFixed(2));
                      const netVal =
                        kpis?.net_balance ??
                        String(
                          Number(financial.data.net_profit ?? 0).toFixed(2),
                        );

                      const rawSeries =
                        rawVis?.income_expense_series ?? rawVis?.series;
                      const series: SeriesPoint[] = Array.isArray(rawSeries)
                        ? (rawSeries as SeriesPoint[])
                        : items.length > 0
                          ? [
                              {
                                period_label: `${start} to ${end}`,
                                income: totalInc,
                                expense: totalExp,
                                net_profit: netVal,
                              },
                            ]
                          : [];

                      const expCats = Array.isArray(rawVis?.expense_categories)
                        ? (
                            rawVis.expense_categories as Array<{
                              label: string;
                              amount: string;
                              share_pct?: string;
                            }>
                          ).map((c) => ({
                            label: c.label,
                            value: c.amount,
                            sub: c.share_pct ? `${c.share_pct}%` : undefined,
                          }))
                        : collapseAccountSlices(items, "expense");

                      const rawTop = rawVis?.top_items ?? rawVis?.top_entries;
                      const topEntries = Array.isArray(rawTop)
                        ? (
                            rawTop as Array<{
                              label: string;
                              amount: string;
                              kind?: string;
                            }>
                          ).map((t) => ({
                            label: t.label,
                            value: t.amount,
                            sub: t.kind,
                          }))
                        : items
                            .filter((r) => Math.abs(Number(r.amount)) > 0)
                            .sort(
                              (a, c) =>
                                Math.abs(Number(c.amount)) -
                                Math.abs(Number(a.amount)),
                            )
                            .slice(0, 6)
                            .map((r) => ({
                              label: `${r.code} · ${r.name}`,
                              value: r.amount,
                              sub: r.kind,
                            }));

                      const cashPts = series
                        .filter((s) => s.running_cash !== undefined)
                        .map((s) => ({
                          label: s.period_label,
                          balance: String(s.running_cash),
                        }));

                      return (
                        <div className="visual-report-block">
                          <GrafanaEmbedBanner
                            grafana={grafanaInfo}
                            showLiveGrafana={showLiveGrafana}
                            onToggleLiveGrafana={setShowLiveGrafana}
                          />
                          <VisualExportBar
                            downloadPdfUrl={`${baseExportUrl}&format=pdf`}
                            downloadHtmlUrl={`${baseExportUrl}&format=html`}
                            onDownloadPdf={() =>
                              downloadVisualMutation.mutate("pdf")
                            }
                            onDownloadHtml={() =>
                              downloadVisualMutation.mutate("html")
                            }
                            downloadBusy={downloadVisualMutation.isPending}
                            onSendEmail={(addr) => {
                              setEmailNotice("");
                              emailVisualMutation.mutate(addr);
                            }}
                            emailBusy={emailVisualMutation.isPending}
                            emailNotice={emailNotice}
                          />
                          <ErrorNotice error={emailVisualMutation.error} />
                          <VisualKpiRow
                            items={[
                              {
                                label: "Total income",
                                value: money(totalInc, b.currency),
                                sub: `${start} through ${end}`,
                              },
                              {
                                label: "Total expenses",
                                value: money(totalExp, b.currency),
                                sub: `${b.basis} basis`,
                              },
                              {
                                label: "Net balance",
                                value: money(netVal, b.currency),
                                sub:
                                  Number(netVal) < 0
                                    ? "Net deficit in period"
                                    : "Positive net margin",
                                deficit: Number(netVal) < 0,
                              },
                              ...(kpis?.cash_balance !== undefined
                                ? [
                                    {
                                      label: "Closing cash",
                                      value: money(
                                        kpis.cash_balance,
                                        b.currency,
                                      ),
                                      sub: `As of ${end}`,
                                      deficit: Number(kpis.cash_balance) < 0,
                                    },
                                  ]
                                : []),
                            ]}
                          />
                          <div className="visual-grid">
                            {showLiveGrafana && embedUrls["5"] ? (
                              <GrafanaIframePanel
                                title="Income vs. expenses over time"
                                src={embedUrls["5"]}
                                fallback={
                                  <IncomeExpenseLineChart
                                    series={series}
                                    currency={b.currency}
                                  />
                                }
                              />
                            ) : (
                              <IncomeExpenseLineChart
                                series={series}
                                currency={b.currency}
                              />
                            )}
                            {showLiveGrafana && embedUrls["6"] ? (
                              <GrafanaIframePanel
                                title="Category spending breakdown"
                                src={embedUrls["6"]}
                                fallback={
                                  <HorizontalBarBreakdown
                                    title="Category spending breakdown"
                                    items={expCats}
                                    currency={b.currency}
                                    barColor="#b86b2b"
                                  />
                                }
                              />
                            ) : (
                              <HorizontalBarBreakdown
                                title="Category spending breakdown"
                                items={expCats}
                                currency={b.currency}
                                barColor="#b86b2b"
                              />
                            )}
                            {showLiveGrafana && embedUrls["7"] ? (
                              <GrafanaIframePanel
                                title="Period comparison"
                                src={embedUrls["7"]}
                                fallback={
                                  <PeriodGroupedBarsChart
                                    title={
                                      periodMode === "yearly"
                                        ? "Yearly comparison"
                                        : "Monthly comparison"
                                    }
                                    series={series}
                                    currency={b.currency}
                                  />
                                }
                              />
                            ) : (
                              <PeriodGroupedBarsChart
                                title={
                                  periodMode === "yearly"
                                    ? "Yearly comparison"
                                    : "Monthly comparison"
                                }
                                series={series}
                                currency={b.currency}
                              />
                            )}
                            {cashPts.length > 0 ? (
                              showLiveGrafana && embedUrls["8"] ? (
                                <GrafanaIframePanel
                                  title="Cash flow & running balance trend"
                                  src={embedUrls["8"]}
                                  fallback={
                                    <CashBalanceTrendChart
                                      points={cashPts}
                                      currency={b.currency}
                                    />
                                  }
                                />
                              ) : (
                                <CashBalanceTrendChart
                                  points={cashPts}
                                  currency={b.currency}
                                />
                              )
                            ) : (
                              <HorizontalBarBreakdown
                                title="Top accounts & transactions"
                                items={topEntries}
                                currency={b.currency}
                              />
                            )}
                          </div>
                        </div>
                      );
                    }

                    // Balance Sheet visuals
                    const assetsSum =
                      kpis?.total_assets !== undefined
                        ? Number(kpis.total_assets)
                        : items
                            .filter((r) => r.kind === "asset")
                            .reduce((acc, r) => acc + Number(r.amount || 0), 0);
                    const liabSum =
                      kpis?.total_liabilities !== undefined
                        ? Number(kpis.total_liabilities)
                        : items
                            .filter((r) => r.kind === "liability")
                            .reduce((acc, r) => acc + Number(r.amount || 0), 0);
                    const retained = Number(
                      (financial.data as { retained_earnings?: string })
                        .retained_earnings ?? 0,
                    );
                    const eqSum =
                      kpis?.total_equity !== undefined
                        ? Number(kpis.total_equity)
                        : items
                            .filter((r) => r.kind === "equity")
                            .reduce(
                              (acc, r) => acc + Number(r.amount || 0),
                              0,
                            ) + retained;
                    const equationDiff = Math.abs(
                      assetsSum - (liabSum + eqSum),
                    );

                    const bsBars = Array.isArray(rawVis?.balance_sheet_sections)
                      ? (
                          rawVis.balance_sheet_sections as Array<{
                            section: string;
                            amount: string;
                          }>
                        ).map((s) => ({
                          label: s.section,
                          value: s.amount,
                          tone:
                            s.section === "Assets"
                              ? ("good" as const)
                              : s.section === "Liabilities"
                                ? ("warn" as const)
                                : undefined,
                        }))
                      : items.length > 0 || retained !== 0
                        ? [
                            {
                              label: "Assets",
                              value: assetsSum.toFixed(2),
                              tone: "good" as const,
                            },
                            {
                              label: "Liabilities",
                              value: liabSum.toFixed(2),
                              tone: "warn" as const,
                            },
                            {
                              label: "Equity (incl. retained earnings)",
                              value: eqSum.toFixed(2),
                            },
                          ]
                        : [];

                    const topBsAccounts = items
                      .filter((r) => Math.abs(Number(r.amount)) > 0)
                      .sort(
                        (a, c) =>
                          Math.abs(Number(c.amount)) -
                          Math.abs(Number(a.amount)),
                      )
                      .slice(0, 6)
                      .map((r) => ({
                        label: `${r.code} · ${r.name}`,
                        value: r.amount,
                        sub: r.kind,
                      }));

                    return (
                      <div className="visual-report-block">
                        <GrafanaEmbedBanner
                          grafana={grafanaInfo}
                          showLiveGrafana={showLiveGrafana}
                          onToggleLiveGrafana={setShowLiveGrafana}
                        />
                        <VisualExportBar
                          downloadPdfUrl={`${baseExportUrl}&format=pdf`}
                          downloadHtmlUrl={`${baseExportUrl}&format=html`}
                          onDownloadPdf={() =>
                            downloadVisualMutation.mutate("pdf")
                          }
                          onDownloadHtml={() =>
                            downloadVisualMutation.mutate("html")
                          }
                          downloadBusy={downloadVisualMutation.isPending}
                          onSendEmail={(addr) => {
                            setEmailNotice("");
                            emailVisualMutation.mutate(addr);
                          }}
                          emailBusy={emailVisualMutation.isPending}
                          emailNotice={emailNotice}
                        />
                        <ErrorNotice error={emailVisualMutation.error} />
                        <VisualKpiRow
                          items={[
                            {
                              label: "Total assets",
                              value: money(assetsSum.toFixed(2), b.currency),
                              sub: `As of ${end}`,
                              deficit: assetsSum < 0,
                            },
                            {
                              label: "Total liabilities",
                              value: money(liabSum.toFixed(2), b.currency),
                              sub: "Obligations owed",
                            },
                            {
                              label: "Total equity",
                              value: money(eqSum.toFixed(2), b.currency),
                              sub: "Includes retained earnings",
                              deficit: eqSum < 0,
                            },
                            {
                              label: "Accounting equation",
                              value:
                                equationDiff <= 0.01
                                  ? "Balanced"
                                  : money(equationDiff.toFixed(2), b.currency),
                              sub: "Assets = Liabilities + Equity",
                              deficit: equationDiff > 0.01,
                            },
                          ]}
                        />
                        <div className="visual-grid">
                          {showLiveGrafana && embedUrls["9"] ? (
                            <GrafanaIframePanel
                              title="Assets vs. liabilities vs. equity"
                              src={embedUrls["9"]}
                              fallback={
                                <HorizontalBarBreakdown
                                  title="Assets vs. liabilities vs. equity"
                                  items={bsBars}
                                  currency={b.currency}
                                />
                              }
                            />
                          ) : (
                            <HorizontalBarBreakdown
                              title="Assets vs. liabilities vs. equity"
                              items={bsBars}
                              currency={b.currency}
                            />
                          )}
                          <HorizontalBarBreakdown
                            title="Top accounts by balance"
                            items={topBsAccounts}
                            currency={b.currency}
                          />
                        </div>
                      </div>
                    );
                  })()}
                </ChartErrorBoundary>
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
                <ChartErrorBoundary>
                  <HorizontalBarBreakdown
                    title="Largest trial balance accounts"
                    currency={b.currency}
                    items={(trial.data.items || [])
                      .map((r) => ({
                        label: `${r.code} · ${r.name}`,
                        value: String(
                          Math.max(Number(r.debit || 0), Number(r.credit || 0)),
                        ),
                        sub:
                          Number(r.debit || 0) >= Number(r.credit || 0)
                            ? "Debit"
                            : "Credit",
                      }))
                      .filter((r) => Number(r.value) > 0)
                      .sort((a, c) => Number(c.value) - Number(a.value))
                      .slice(0, 6)}
                  />
                </ChartErrorBoundary>
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
                <ChartErrorBoundary>
                  <div className="visual-grid">
                    <CashBalanceTrendChart
                      title="Projected 13-week closing cash"
                      currency={b.currency}
                      points={(forecast.data.weeks || []).map((w) => ({
                        label: w.start_date.slice(5),
                        balance: w.closing_cash,
                      }))}
                    />
                    <PeriodGroupedBarsChart
                      title="Weekly receipts vs. payments"
                      currency={b.currency}
                      series={(forecast.data.weeks || []).map((w) => ({
                        period_label: w.start_date.slice(5),
                        income: w.receipts,
                        expense: w.payments,
                        net_profit: String(
                          (Number(w.receipts) - Number(w.payments)).toFixed(2),
                        ),
                      }))}
                    />
                  </div>
                </ChartErrorBoundary>
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
                    key={`answers-${forecast.data.report_id}`}
                    reportId={forecast.data.report_id}
                  />
                )}
                {b.p2_insights_available && (
                  <ForecastEvaluation
                    key={`evaluation-${forecast.data.report_id}`}
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

function ForecastEvaluation({ reportId }: { reportId: string }) {
  const business = useBusiness();
  const interval = useMutation({
    mutationFn: ({ id, week }: { id: string; week: number }) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/reports/forecast/{report_id}/interval",
          {
            params: {
              path: { business_id: business.id, report_id: id },
              query: { week },
            },
          },
        ),
      ),
  });
  const evaluation = useMutation({
    mutationFn: (id: string) =>
      result(
        api.GET(
          "/api/v1/businesses/{business_id}/reports/forecast/{report_id}/evaluation",
          {
            params: { path: { business_id: business.id, report_id: id } },
          },
        ),
      ),
  });
  const data = evaluation.data;
  return (
    <details className="chart-data">
      <summary>Compare a saved forecast with actual cash</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          evaluation.mutate(
            String(new FormData(event.currentTarget).get("report_id")).trim(),
          );
        }}
      >
        <Field label="Baseline forecast report ID">
          <input name="report_id" required defaultValue={reportId} />
        </Field>
        <Button variant="secondary" busy={evaluation.isPending}>
          Compare with ledger
        </Button>
      </form>
      <ErrorNotice error={evaluation.error} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          interval.mutate({
            id: String(form.get("report_id")).trim(),
            week: Number(form.get("week")),
          });
        }}
      >
        <h3>Historical uncertainty interval</h3>
        <p className="muted">
          Uses 20 non-overlapping calibration periods and 10 later validation
          periods. If history is insufficient or validation fails, no interval
          is shown.
        </p>
        <Field label="Forecast report for calibration">
          <input name="report_id" required defaultValue={reportId} />
        </Field>
        <Field label="Forecast week">
          <input
            name="week"
            type="number"
            min={1}
            max={13}
            required
            defaultValue={1}
          />
        </Field>
        <Button variant="secondary" busy={interval.isPending}>
          Evaluate uncertainty
        </Button>
      </form>
      <ErrorNotice error={interval.error} />
      {interval.data && (
        <div role="status">
          <p>
            {interval.data.status === "available"
              ? `Historical 80% interval: ${money(interval.data.lower_cash!, business.currency)} to ${money(interval.data.upper_cash!, business.currency)}.`
              : interval.data.status === "validation_failed"
                ? "Recent validation did not meet the coverage target. No interval is available."
                : "Not enough matching, non-overlapping history to estimate an interval."}
          </p>
          <p>
            Calibration samples: {interval.data.calibration_count}. Validation
            samples: {interval.data.validation_count}.
          </p>
          <p className="muted">{interval.data.limitations}</p>
        </div>
      )}
      {data && (
        <>
          <p role="status">
            {data.mean_absolute_error === null
              ? "No weeks have ended for this forecast."
              : `Average absolute difference across ${data.weeks.length} completed weeks: ${money(data.mean_absolute_error, data.currency)}.`}
          </p>
          {data.weeks.length > 0 && (
            <Table
              caption="Forecast compared with posted cash balances"
              rows={data.weeks}
              rowKey={(row) => row.end_date}
              columns={[
                { label: "Week ended", render: (row) => row.end_date },
                {
                  label: "Forecast",
                  render: (row) => money(row.forecast_cash, data.currency),
                },
                {
                  label: "Posted cash",
                  render: (row) => money(row.ledger_cash, data.currency),
                },
                {
                  label: "Difference",
                  render: (row) => money(row.difference, data.currency),
                },
              ]}
            />
          )}
          <p className="report-footnote">Baseline report {data.report_id}</p>
        </>
      )}
    </details>
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
