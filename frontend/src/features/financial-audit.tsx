import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  ChartErrorBoundary,
  GrafanaEmbedBanner,
  GrafanaIframePanel,
  type GrafanaStatusInfo,
  HorizontalBarBreakdown,
  VisualExportBar,
} from "../components/report-charts";
import {
  Badge,
  Button,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  NextPage,
  PageHeading,
  Table,
  formatWhen,
  humanize,
} from "../components/ui";
import { useBusiness } from "./context";

function triggerAuditDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

type ManualMode = "monthly" | "yearly" | "emergency";

const MODE_LABELS: Record<string, string> = {
  monthly: "Monthly audit",
  yearly: "Yearly audit",
  emergency: "Full on-demand audit",
  auto_balance_sheet: "Balance sheet verification",
  auto_period_close: "Period close verification",
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: "Run manually",
  background_job: "Background run",
  balance_sheet_generated: "Balance sheet generated",
  period_locked: "Period locked",
  close_package_prepared: "Close package prepared",
};

const HEALTH_LABELS: Record<string, string> = {
  clean_verified: "Books verified clean",
  clean_empty_period: "No activity in period",
  minor_observations: "Minor observations",
  attention_needed: "Needs review",
  critical_attention_required: "Action required",
  queued: "Running in background",
};

function severityTone(severity: string): "neutral" | "good" | "warning" {
  if (severity === "Critical" || severity === "High") return "warning";
  if (severity === "Low") return "neutral";
  return "neutral";
}

function healthTone(status: string): "neutral" | "good" | "warning" {
  if (status === "clean_verified" || status === "clean_empty_period") {
    return "good";
  }
  if (
    status === "critical_attention_required" ||
    status === "attention_needed"
  ) {
    return "warning";
  }
  return "neutral";
}

export function FinancialAudit() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const now = new Date();
  const defaultMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

  const [mode, setMode] = useState<ManualMode>("monthly");
  const [month, setMonth] = useState(defaultMonth);
  const [year, setYear] = useState(String(now.getFullYear()));
  const [calendarYear, setCalendarYear] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [runAsync, setRunAsync] = useState(false);
  const [cursor, setCursor] = useState<string | undefined>();
  const [filterMode, setFilterMode] = useState<string>("");
  const [selectedAuditId, setSelectedAuditId] = useState<string | undefined>();
  const [showLiveGrafana, setShowLiveGrafana] = useState(false);
  const [emailNotice, setEmailNotice] = useState("");

  const runsQuery = useQuery({
    queryKey: [...keys.business(b.id), "financial-audits", filterMode, cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/audits", {
          signal,
          params: {
            path,
            query: {
              limit: 15,
              cursor,
              audit_type: filterMode
                ? (filterMode as
                    | "monthly"
                    | "yearly"
                    | "emergency"
                    | "auto_balance_sheet"
                    | "auto_period_close")
                : undefined,
            },
          },
        }),
      ),
  });

  const activeAuditId = selectedAuditId ?? runsQuery.data?.items[0]?.id;

  const detailQuery = useQuery({
    queryKey: [...keys.business(b.id), "financial-audit-detail", activeAuditId],
    enabled: Boolean(activeAuditId),
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/audits/{audit_id}", {
          signal,
          params: { path: { business_id: b.id, audit_id: activeAuditId! } },
        }),
      ),
  });

  const graphQuery = useQuery({
    queryKey: [...keys.business(b.id), "financial-audit-graph"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/audits/graph", {
          signal,
          params: { path },
        }),
      ),
  });

  const runAudit = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/audits", {
          params: { path },
          body: {
            audit_type: mode,
            month: mode === "monthly" ? month : null,
            year: mode === "yearly" ? Number(year) : null,
            calendar_year: mode === "yearly" ? calendarYear : false,
            start_date:
              mode === "emergency" && startDate ? startDate : undefined,
            end_date: mode === "emergency" && endDate ? endDate : undefined,
            run_async: runAsync,
          },
        }),
      ),
    onSuccess: (created) => {
      setSelectedAuditId(created.id);
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "financial-audits"],
      });
      void queryClient.invalidateQueries({
        queryKey: [
          ...keys.business(b.id),
          "financial-audit-detail",
          created.id,
        ],
      });
    },
  });

  const activeReport = runAudit.data ?? detailQuery.data;

  const auditVisualsQuery = useQuery({
    queryKey: [
      ...keys.business(b.id),
      "financial-audit-visuals",
      activeReport?.id,
      filterMode,
    ],
    enabled: Boolean(activeReport?.id),
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/audits/visuals", {
          signal,
          params: {
            path,
            query: {
              audit_id: activeReport?.id,
              audit_type: filterMode || "all",
            },
          },
        }),
      ),
  });

  const downloadAuditMutation = useMutation({
    mutationFn: async (fmt: "pdf" | "html") => {
      if (!activeReport) return;
      const res = await api.GET(
        "/api/v1/businesses/{business_id}/audits/{audit_id}/export",
        {
          params: {
            path: { business_id: b.id, audit_id: activeReport.id },
            query: { format: fmt, theme: "light" },
          },
          parseAs: "blob",
        },
      );
      if (!res.response.ok || !res.data) {
        throw new Error("Could not export audit report.");
      }
      triggerAuditDownload(
        res.data as Blob,
        `financial-audit-${activeReport.period_end}.${fmt}`,
      );
    },
  });

  const emailAuditMutation = useMutation({
    mutationFn: (recipientEmail: string) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/audits/{audit_id}/email", {
          params: {
            path: { business_id: b.id, audit_id: activeReport!.id },
          },
          body: { recipient_email: recipientEmail, theme: "light" },
        }),
      ),
    onSuccess: (_res, recipientEmail) => {
      setEmailNotice(`Visual audit report sent to ${recipientEmail}.`);
    },
  });

  return (
    <>
      <PageHeading
        eyebrow="FINANCIAL INTEGRITY & COMPLIANCE"
        title="Financial audit"
        description="Verify balances, period continuity, bank reconciliations, and workspace accounting rules in plain language without altering your books."
      />

      <ErrorNotice
        error={runsQuery.error || detailQuery.error || runAudit.error}
      />

      <div className="dashboard-grid">
        <section className="panel settings-section">
          <h2>Run a financial check</h2>
          <p className="muted">
            Every calculation is checked directly against your posted ledger,
            bank statements, and active workspace rules. Nothing in your books
            is modified.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              runAudit.mutate();
            }}
          >
            <div className="form-grid">
              <Field label="Audit scope">
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as ManualMode)}
                >
                  <option value="monthly">Monthly audit</option>
                  <option value="yearly">Yearly audit</option>
                  <option value="emergency">Full or custom-range audit</option>
                </select>
              </Field>

              {mode === "monthly" && (
                <Field
                  label="Month to audit"
                  hint="Checks all entries, bank rows, and balances in this month."
                >
                  <input
                    type="month"
                    required
                    value={month}
                    onChange={(e) => setMonth(e.target.value)}
                  />
                </Field>
              )}

              {mode === "yearly" && (
                <Field
                  label="Year to audit"
                  hint="Follows your workspace fiscal year unless calendar year is selected."
                >
                  <input
                    type="number"
                    min={1900}
                    max={2200}
                    required
                    value={year}
                    onChange={(e) => setYear(e.target.value)}
                  />
                </Field>
              )}

              {mode === "emergency" && (
                <>
                  <Field
                    label="Start date (optional)"
                    hint="Leave blank to scan from your earliest recorded entry."
                  >
                    <input
                      type="date"
                      value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                    />
                  </Field>
                  <Field
                    label="End date (optional)"
                    hint="Leave blank to scan through today."
                  >
                    <input
                      type="date"
                      value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                    />
                  </Field>
                </>
              )}
            </div>

            {mode === "yearly" && (
              <label className="checkbox-row" style={{ marginTop: "0.75rem" }}>
                <input
                  type="checkbox"
                  checked={calendarYear}
                  onChange={(e) => setCalendarYear(e.target.checked)}
                />
                <span>
                  Use calendar year (January 1 to December 31) instead of fiscal
                  year
                </span>
              </label>
            )}

            <label className="checkbox-row" style={{ marginTop: "0.75rem" }}>
              <input
                type="checkbox"
                checked={runAsync}
                onChange={(e) => setRunAsync(e.target.checked)}
              />
              <span>
                Run in background (recommended for multi-year transaction
                histories)
              </span>
            </label>

            <div className="actions" style={{ marginTop: "1rem" }}>
              <Button type="submit" busy={runAudit.isPending}>
                Run financial audit
              </Button>
            </div>
          </form>
        </section>

        <section className="panel settings-section">
          <h2>Automatic safeguards</h2>
          <p className="muted">
            In addition to manual monthly, yearly, and on-demand checks,
            LedgerPilot automatically verifies your books whenever you:
          </p>
          <ul>
            <li>
              <strong>Generate a Balance Sheet</strong> — confirms that total
              assets match liabilities plus equity and retained earnings.
            </li>
            <li>
              <strong>Lock an accounting period</strong> — checks opening and
              closing continuity, subledger control accounts, and mandatory
              close sign-offs.
            </li>
            <li>
              <strong>Prepare a close package</strong> — records a point-in-time
              verification report alongside your period archive.
            </li>
          </ul>
          {graphQuery.data && (
            <details style={{ marginTop: "0.75rem" }}>
              <summary>How each audit is verified</summary>
              <p className="muted" style={{ marginTop: "0.5rem" }}>
                Active checks:{" "}
                {graphQuery.data.tools.map((tool) => humanize(tool)).join(", ")}
                .
              </p>
            </details>
          )}
        </section>
      </div>

      {detailQuery.isPending && !activeReport ? (
        <Loading />
      ) : activeReport ? (
        <section
          className="panel settings-section"
          style={{ marginTop: "1.25rem" }}
          aria-label="Selected audit report"
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "space-between",
              gap: "0.75rem",
              alignItems: "center",
            }}
          >
            <div>
              <h2>
                {MODE_LABELS[activeReport.audit_type] ||
                  humanize(activeReport.audit_type)}{" "}
                ({activeReport.period_start} to {activeReport.period_end})
              </h2>
              <p className="muted">
                {TRIGGER_LABELS[activeReport.trigger_source] ||
                  humanize(activeReport.trigger_source)}{" "}
                · Checked {formatWhen(activeReport.created_at)}
              </p>
            </div>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <Badge
                tone={healthTone(activeReport.severity_summary.health_status)}
              >
                {HEALTH_LABELS[activeReport.severity_summary.health_status] ||
                  humanize(activeReport.severity_summary.health_status)}
              </Badge>
              <Badge tone="neutral">
                {activeReport.polished_report.fallback_used
                  ? "Standard plain-language summary"
                  : "AI-polished plain-language summary"}
              </Badge>
            </div>
          </div>

          <div
            className="kpi-grid"
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
              gap: "0.75rem",
              marginTop: "1rem",
            }}
          >
            <div className="panel">
              <small className="muted">Critical issues</small>
              <strong style={{ display: "block", fontSize: "1.35rem" }}>
                {activeReport.severity_summary.critical}
              </strong>
            </div>
            <div className="panel">
              <small className="muted">High priority</small>
              <strong style={{ display: "block", fontSize: "1.35rem" }}>
                {activeReport.severity_summary.high}
              </strong>
            </div>
            <div className="panel">
              <small className="muted">Medium priority</small>
              <strong style={{ display: "block", fontSize: "1.35rem" }}>
                {activeReport.severity_summary.medium}
              </strong>
            </div>
            <div className="panel">
              <small className="muted">Low / observations</small>
              <strong style={{ display: "block", fontSize: "1.35rem" }}>
                {activeReport.severity_summary.low}
              </strong>
            </div>
          </div>

          <ChartErrorBoundary>
            {(() => {
              const rawVis = auditVisualsQuery.data as
                Record<string, unknown> | undefined;
              const grafanaInfo =
                rawVis &&
                typeof rawVis.grafana === "object" &&
                rawVis.grafana !== null
                  ? (rawVis.grafana as GrafanaStatusInfo)
                  : undefined;
              const embedUrls = grafanaInfo?.embed_urls || {};
              const exportBase = `/api/v1/businesses/${b.id}/audits/${activeReport.id}/export`;

              const rawSev = rawVis?.by_severity ?? rawVis?.severity_bars;
              const sevBars = Array.isArray(rawSev)
                ? (
                    rawSev as Array<{
                      severity: string;
                      count: number;
                    }>
                  ).map((s) => ({
                    label: s.severity,
                    value: s.count,
                    tone:
                      s.severity === "Critical"
                        ? ("danger" as const)
                        : s.severity === "High" || s.severity === "Medium"
                          ? ("warn" as const)
                          : ("good" as const),
                  }))
                : [
                    {
                      label: "Critical",
                      value: activeReport.severity_summary.critical,
                      tone: "danger" as const,
                    },
                    {
                      label: "High",
                      value: activeReport.severity_summary.high,
                      tone: "warn" as const,
                    },
                    {
                      label: "Medium",
                      value: activeReport.severity_summary.medium,
                      tone: "warn" as const,
                    },
                    {
                      label: "Low",
                      value: activeReport.severity_summary.low,
                      tone: "good" as const,
                    },
                  ];

              const catCounts: Record<string, number> = {};
              for (const f of activeReport.findings || []) {
                const key = humanize(f.category);
                catCounts[key] = (catCounts[key] || 0) + 1;
              }
              const rawCat = rawVis?.by_category ?? rawVis?.category_bars;
              const catBars = Array.isArray(rawCat)
                ? (
                    rawCat as Array<{
                      category: string;
                      count: number;
                    }>
                  ).map((c) => ({
                    label: c.category,
                    value: c.count,
                  }))
                : Object.entries(catCounts).map(([label, value]) => ({
                    label,
                    value,
                  }));

              const rawTimeline = rawVis?.issues_over_time ?? rawVis?.timeline;
              const timelineBars = Array.isArray(rawTimeline)
                ? (
                    rawTimeline as Array<{
                      period_label?: string;
                      period_end?: string;
                      audit_type: string;
                      total: number;
                      critical: number;
                    }>
                  ).map((t) => ({
                    label: `${t.period_label || t.period_end} (${MODE_LABELS[t.audit_type] || humanize(t.audit_type)})`,
                    value: t.total,
                    tone: t.critical > 0 ? ("danger" as const) : undefined,
                  }))
                : (runsQuery.data?.items || []).slice(0, 8).map((r) => ({
                    label: `${r.period_end} (${MODE_LABELS[r.audit_type] || humanize(r.audit_type)})`,
                    value: r.severity_summary.total,
                    tone:
                      r.severity_summary.critical > 0
                        ? ("danger" as const)
                        : undefined,
                  }));

              return (
                <div
                  className="visual-report-block"
                  style={{ marginTop: "1rem" }}
                >
                  <GrafanaEmbedBanner
                    grafana={grafanaInfo}
                    showLiveGrafana={showLiveGrafana}
                    onToggleLiveGrafana={setShowLiveGrafana}
                  />
                  <VisualExportBar
                    downloadPdfUrl={`${exportBase}?format=pdf`}
                    downloadHtmlUrl={`${exportBase}?format=html`}
                    onDownloadPdf={() => downloadAuditMutation.mutate("pdf")}
                    onDownloadHtml={() => downloadAuditMutation.mutate("html")}
                    downloadBusy={downloadAuditMutation.isPending}
                    onSendEmail={(addr) => {
                      setEmailNotice("");
                      emailAuditMutation.mutate(addr);
                    }}
                    emailBusy={emailAuditMutation.isPending}
                    emailNotice={emailNotice}
                  />
                  <ErrorNotice error={emailAuditMutation.error} />
                  <div className="visual-grid">
                    {showLiveGrafana && embedUrls["5"] ? (
                      <GrafanaIframePanel
                        title="Audit issues by severity"
                        src={embedUrls["5"]}
                        fallback={
                          <HorizontalBarBreakdown
                            title="Audit issues by severity"
                            items={sevBars}
                            isCount
                          />
                        }
                      />
                    ) : (
                      <HorizontalBarBreakdown
                        title="Audit issues by severity"
                        items={sevBars}
                        isCount
                      />
                    )}
                    {showLiveGrafana && embedUrls["6"] ? (
                      <GrafanaIframePanel
                        title="Issues by category"
                        src={embedUrls["6"]}
                        fallback={
                          <HorizontalBarBreakdown
                            title="Issues by category"
                            items={catBars.length > 0 ? catBars : []}
                            isCount={catBars.length > 0}
                            emptyText="No issues found for this period"
                          />
                        }
                      />
                    ) : (
                      <HorizontalBarBreakdown
                        title="Issues by category"
                        items={catBars.length > 0 ? catBars : []}
                        isCount={catBars.length > 0}
                        emptyText="No issues found for this period"
                      />
                    )}
                    {showLiveGrafana && embedUrls["7"] ? (
                      <GrafanaIframePanel
                        title="Audit issues over time"
                        src={embedUrls["7"]}
                        fallback={
                          <HorizontalBarBreakdown
                            title="Audit issues over time"
                            items={timelineBars}
                            isCount
                          />
                        }
                      />
                    ) : (
                      <HorizontalBarBreakdown
                        title="Audit issues over time"
                        items={timelineBars}
                        isCount
                      />
                    )}
                  </div>
                </div>
              );
            })()}
          </ChartErrorBoundary>

          <div style={{ marginTop: "1rem" }}>
            <h3>Executive summary</h3>
            <p>{activeReport.polished_report.executive_summary}</p>
          </div>

          {activeReport.polished_report.next_steps.length > 0 && (
            <div style={{ marginTop: "1rem" }}>
              <h3>Recommended next steps</h3>
              <ol>
                {activeReport.polished_report.next_steps.map((step, idx) => (
                  <li key={idx}>{step}</li>
                ))}
              </ol>
            </div>
          )}

          <div style={{ marginTop: "1.25rem" }}>
            <h3>Findings ({activeReport.findings.length})</h3>
            {activeReport.findings.length === 0 ? (
              <Empty title="No accounting discrepancies found">
                All balances, control accounts, and active workspace rules
                reconciled cleanly for this period.
              </Empty>
            ) : (
              <Table
                rows={activeReport.findings}
                rowKey={(f) => f.id}
                caption="Financial audit findings"
                columns={[
                  {
                    label: "Severity",
                    render: (f) => (
                      <Badge tone={severityTone(f.severity)}>
                        {f.severity}
                      </Badge>
                    ),
                  },
                  {
                    label: "Area",
                    render: (f) => humanize(f.category),
                  },
                  {
                    label: "What we found",
                    render: (f) => {
                      const narrative =
                        activeReport.polished_report.findings_narrative.find(
                          (n) => n.id === f.id,
                        );
                      return (
                        <div>
                          <strong>
                            {narrative?.what || humanize(f.rule_violated)}
                          </strong>
                          <div className="muted">
                            Expected:{" "}
                            {f.expected_vs_actual.expected || "Balanced"} ·
                            Actual: {f.expected_vs_actual.actual || "Mismatch"}
                            {f.expected_vs_actual.difference
                              ? ` · Difference: ${f.expected_vs_actual.difference}`
                              : ""}
                          </div>
                        </div>
                      );
                    },
                  },
                  {
                    label: "How to resolve",
                    render: (f) => {
                      const narrative =
                        activeReport.polished_report.findings_narrative.find(
                          (n) => n.id === f.id,
                        );
                      return (
                        <div>
                          <p style={{ margin: 0 }}>
                            {narrative?.recommended_fix || f.recommendation}
                          </p>
                          {f.evidence.length > 0 && (
                            <details style={{ marginTop: "0.35rem" }}>
                              <summary>Supporting details</summary>
                              <ul>
                                {f.evidence.map((ev, i) => (
                                  <li key={i}>{ev}</li>
                                ))}
                              </ul>
                            </details>
                          )}
                        </div>
                      );
                    },
                  },
                ]}
              />
            )}
          </div>
        </section>
      ) : null}

      <section
        className="panel settings-section"
        style={{ marginTop: "1.25rem" }}
      >
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "0.75rem",
            marginBottom: "0.75rem",
          }}
        >
          <h2>Audit history</h2>
          <Field label="Filter by mode">
            <select
              value={filterMode}
              onChange={(e) => {
                setFilterMode(e.target.value);
                setCursor(undefined);
              }}
            >
              <option value="">All audit runs</option>
              <option value="monthly">Monthly audits</option>
              <option value="yearly">Yearly audits</option>
              <option value="emergency">Full on-demand audits</option>
              <option value="auto_balance_sheet">Balance sheet checks</option>
              <option value="auto_period_close">Period close checks</option>
            </select>
          </Field>
        </div>

        {runsQuery.isPending ? (
          <Loading />
        ) : (
          <>
            <Table
              rows={runsQuery.data?.items || []}
              rowKey={(item) => item.id}
              caption="Past financial audit runs"
              emptyText="No financial audits have been run yet. Choose a month, year, or custom range above to start."
              columns={[
                {
                  label: "Period",
                  render: (item) =>
                    `${item.period_start} to ${item.period_end}`,
                },
                {
                  label: "Scope",
                  render: (item) =>
                    MODE_LABELS[item.audit_type] || humanize(item.audit_type),
                },
                {
                  label: "Trigger",
                  render: (item) =>
                    TRIGGER_LABELS[item.trigger_source] ||
                    humanize(item.trigger_source),
                },
                {
                  label: "Status",
                  render: (item) => (
                    <Badge
                      tone={healthTone(item.severity_summary.health_status)}
                    >
                      {HEALTH_LABELS[item.severity_summary.health_status] ||
                        humanize(item.status)}
                    </Badge>
                  ),
                },
                {
                  label: "Findings",
                  render: (item) =>
                    item.severity_summary.total === 0
                      ? "None"
                      : `${item.severity_summary.total} (${item.severity_summary.critical} critical)`,
                },
                {
                  label: "Checked",
                  render: (item) => formatWhen(item.created_at),
                },
                {
                  label: "Report",
                  render: (item) => (
                    <Button
                      type="button"
                      variant={
                        item.id === activeReport?.id ? "primary" : "secondary"
                      }
                      onClick={() => setSelectedAuditId(item.id)}
                    >
                      {item.id === activeReport?.id ? "Viewing" : "Open report"}
                    </Button>
                  ),
                },
              ]}
            />
            <NextPage
              cursor={runsQuery.data?.next_cursor}
              onNext={() => setCursor(runsQuery.data?.next_cursor ?? undefined)}
              onReset={() => setCursor(undefined)}
            />
          </>
        )}
      </section>
    </>
  );
}
