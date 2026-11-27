import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
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
