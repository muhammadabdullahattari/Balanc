import { Component, type ReactNode, useState } from "react";
import { Badge, Button, Field, money } from "./ui";

export class ChartErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="notice" role="status">
          Visual chart summary is temporarily unavailable — exact figures are
          shown in the table below.
        </div>
      );
    }
    return this.props.children;
  }
}

export interface SeriesPoint {
  period_label: string;
  income: string;
  expense: string;
  net_profit: string;
  running_cash?: string;
}

export interface CategorySlice {
  label: string;
  amount: string;
  share_pct?: string;
}

export interface BalanceSheetSection {
  section: string;
  amount: string;
}

export interface TopEntryItem {
  entry_date?: string;
  label: string;
  kind?: string;
  amount: string;
}

export interface GrafanaStatusInfo {
  configured?: boolean;
  reachable?: boolean;
  embed_enabled?: boolean;
  dashboard_uid?: string;
  visual_source?: string;
  status_message?: string;
  embed_urls?: Record<string, string>;
}

const CHART_COLORS = {
  navy: "#274b7a",
  emerald: "#1f7a5c",
  copper: "#b86b2b",
  slate: "#4c638a",
  crimson: "#b3353e",
};

function num(v: string | number | undefined): number {
  const parsed = Number(v ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function VisualKpiRow({
  items,
}: {
  items: Array<{
    label: string;
    value: string;
    sub?: string;
    deficit?: boolean;
  }>;
}) {
  return (
    <div
      className="visual-kpi-grid"
      role="region"
      aria-label="Headline figures"
    >
      {items.map((it) => (
        <div
          key={it.label}
          className={`visual-kpi-card${it.deficit ? " is-deficit" : ""}`}
        >
          <span className="visual-kpi-label">{it.label}</span>
          <strong className="visual-kpi-value">{it.value}</strong>
          {it.sub && <small className="visual-kpi-sub">{it.sub}</small>}
        </div>
      ))}
    </div>
  );
}

export function GrafanaEmbedBanner({
  grafana,
  showLiveGrafana,
  onToggleLiveGrafana,
}: {
  grafana?: GrafanaStatusInfo;
  showLiveGrafana: boolean;
  onToggleLiveGrafana: (next: boolean) => void;
}) {
  if (!grafana) return null;
  const canEmbed = Boolean(
    grafana.embed_enabled &&
    grafana.reachable &&
    grafana.embed_urls &&
    Object.keys(grafana.embed_urls).length > 0,
  );
  return (
    <div className="grafana-status-strip" role="status">
      <div className="grafana-status-text">
        <Badge tone={grafana.reachable ? "good" : "neutral"}>
          {grafana.reachable ? "Grafana connected" : "Server-verified charts"}
        </Badge>
        <span>
          {grafana.status_message ||
            "Charts are computed directly from posted ledger figures."}
        </span>
      </div>
      {canEmbed && (
        <Button
          type="button"
          variant="secondary"
          onClick={() => onToggleLiveGrafana(!showLiveGrafana)}
        >
          {showLiveGrafana
            ? "Show built-in charts"
            : "Show live Grafana panels"}
        </Button>
      )}
    </div>
  );
}

export function GrafanaIframePanel({
  title,
  src,
  fallback,
}: {
  title: string;
  src?: string;
  fallback: React.ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return <>{fallback}</>;
  }
  return (
    <div className="visual-card">
      <div className="visual-card-head">
        <h3>{title}</h3>
        <Badge tone="good">Grafana panel</Badge>
      </div>
      <iframe
        title={title}
        src={src}
        className="grafana-panel-iframe"
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function IncomeExpenseLineChart({
  title = "Income vs. expenses over time",
  series,
  currency,
}: {
  title?: string;
  series: SeriesPoint[];
  currency: string;
}) {
  if (!series.length) {
    return (
      <div className="visual-card">
        <h3>{title}</h3>
        <p className="visual-empty">No data for this period</p>
      </div>
    );
  }

  const allVals = series.flatMap((s) => [
    num(s.income),
    num(s.expense),
    num(s.net_profit),
    0,
  ]);
  const minVal = Math.min(0, ...allVals);
  const maxVal = Math.max(1, ...allVals);
  const span = Math.max(1, maxVal - minVal);

  const width = 540;
  const height = 210;
  const padL = 52;
  const padR = 18;
  const padT = 16;
  const padB = 34;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const xAt = (idx: number) =>
    series.length === 1
      ? padL + plotW / 2
      : padL + (idx / (series.length - 1)) * plotW;
  const yAt = (val: number) => padT + ((maxVal - val) / span) * plotH;
  const zeroY = yAt(0);

  const buildPath = (getter: (pt: SeriesPoint) => number) =>
    series
      .map(
        (pt, idx) =>
          `${idx === 0 ? "M" : "L"}${xAt(idx).toFixed(1)},${yAt(getter(pt)).toFixed(1)}`,
      )
      .join(" ");

  const incPath = buildPath((p) => num(p.income));
  const expPath = buildPath((p) => num(p.expense));
  const netPath = buildPath((p) => num(p.net_profit));

  return (
    <div className="visual-card">
      <div className="visual-card-head">
        <h3>{title}</h3>
        <span className="muted">{currency}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="visual-svg"
        role="img"
        aria-label={`${title} in ${currency}`}
      >
        {[0, 0.5, 1].map((t) => {
          const v = minVal + (1 - t) * span;
          const y = padT + t * plotH;
          return (
            <g key={t}>
              <line
                x1={padL}
                x2={width - padR}
                y1={y}
                y2={y}
                className="visual-grid-line"
              />
              <text
                x={padL - 6}
                y={y + 4}
                textAnchor="end"
                className="visual-axis-text"
              >
                {Math.round(v).toLocaleString()}
              </text>
            </g>
          );
        })}
        <line
          x1={padL}
          x2={width - padR}
          y1={zeroY}
          y2={zeroY}
          stroke={CHART_COLORS.crimson}
          strokeDasharray="3 3"
          strokeWidth="0.9"
        />
        {series.length > 1 && (
          <>
            <path
              d={incPath}
              fill="none"
              stroke={CHART_COLORS.emerald}
              strokeWidth="2.4"
            />
            <path
              d={expPath}
              fill="none"
              stroke={CHART_COLORS.copper}
              strokeWidth="2.4"
            />
            <path
              d={netPath}
              fill="none"
              stroke={CHART_COLORS.navy}
              strokeWidth="2"
              strokeDasharray="4 3"
            />
          </>
        )}
        {series.map((pt, idx) => {
          const cx = xAt(idx);
          const showLabel =
            series.length <= 6 ||
            idx === 0 ||
            idx === series.length - 1 ||
            idx % Math.ceil(series.length / 5) === 0;
          return (
            <g key={pt.period_label}>
              <circle
                cx={cx}
                cy={yAt(num(pt.income))}
                r="3.8"
                fill={CHART_COLORS.emerald}
              >
                <title>{`${pt.period_label} Income: ${money(pt.income, currency)}`}</title>
              </circle>
              <circle
                cx={cx}
                cy={yAt(num(pt.expense))}
                r="3.8"
                fill={CHART_COLORS.copper}
              >
                <title>{`${pt.period_label} Expenses: ${money(pt.expense, currency)}`}</title>
              </circle>
              <circle
                cx={cx}
                cy={yAt(num(pt.net_profit))}
                r="3.2"
                fill={
                  num(pt.net_profit) < 0
                    ? CHART_COLORS.crimson
                    : CHART_COLORS.navy
                }
              >
                <title>{`${pt.period_label} Net: ${money(pt.net_profit, currency)}`}</title>
              </circle>
              {showLabel && (
                <text
                  x={cx}
                  y={height - 8}
                  textAnchor="middle"
                  className="visual-axis-text"
                >
                  {pt.period_label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="visual-legend">
        <span>
          <i style={{ background: CHART_COLORS.emerald }} /> Income
        </span>
        <span>
          <i style={{ background: CHART_COLORS.copper }} /> Expenses
        </span>
        <span>
          <i style={{ background: CHART_COLORS.navy }} /> Net balance
        </span>
      </div>
    </div>
  );
}

export function PeriodGroupedBarsChart({
  title = "Period comparison",
  series,
  currency,
}: {
  title?: string;
  series: SeriesPoint[];
  currency: string;
}) {
  if (!series.length) {
    return (
      <div className="visual-card">
        <h3>{title}</h3>
        <p className="visual-empty">No data for this period</p>
      </div>
    );
  }
  const maxVal = Math.max(
    1,
    ...series.flatMap((s) => [
      Math.abs(num(s.income)),
      Math.abs(num(s.expense)),
    ]),
  );
  return (
    <div className="visual-card">
      <div className="visual-card-head">
        <h3>{title}</h3>
        <span className="muted">{currency}</span>
      </div>
      <div className="grouped-bar-strip" role="img" aria-label={title}>
        {series.map((pt) => {
          const incPct = Math.min(
            100,
            (Math.max(0, num(pt.income)) / maxVal) * 100,
          );
          const expPct = Math.min(
            100,
            (Math.max(0, num(pt.expense)) / maxVal) * 100,
          );
          return (
            <div className="grouped-bar-col" key={pt.period_label}>
              <div className="grouped-bar-pair">
                <span
                  style={{
                    height: `${Math.max(incPct, num(pt.income) > 0 ? 4 : 0)}%`,
                    background: CHART_COLORS.emerald,
                  }}
                  title={`${pt.period_label} Income: ${money(pt.income, currency)}`}
                />
                <span
                  style={{
                    height: `${Math.max(expPct, num(pt.expense) > 0 ? 4 : 0)}%`,
                    background: CHART_COLORS.copper,
                  }}
                  title={`${pt.period_label} Expenses: ${money(pt.expense, currency)}`}
                />
              </div>
              <small>{pt.period_label}</small>
            </div>
          );
        })}
      </div>
      <div className="visual-legend">
        <span>
          <i style={{ background: CHART_COLORS.emerald }} /> Income
        </span>
        <span>
          <i style={{ background: CHART_COLORS.copper }} /> Expenses
        </span>
      </div>
    </div>
  );
}

export function HorizontalBarBreakdown({
  title,
  items,
  currency,
  isCount = false,
  barColor = CHART_COLORS.navy,
  emptyText = "No data for this period",
}: {
  title: string;
  items: Array<{
    label: string;
    value: string | number;
    sub?: string;
    tone?: "danger" | "warn" | "good";
  }>;
  currency?: string;
  isCount?: boolean;
  barColor?: string;
  emptyText?: string;
}) {
  const nonZero = items.filter((i) => Math.abs(num(i.value)) > 0 || isCount);
  if (!items.length || (!isCount && nonZero.length === 0)) {
    return (
      <div className="visual-card">
        <h3>{title}</h3>
        <p className="visual-empty">{emptyText}</p>
      </div>
    );
  }
  const maxVal = Math.max(1, ...items.map((i) => Math.abs(num(i.value))));
  return (
    <div className="visual-card">
      <div className="visual-card-head">
        <h3>{title}</h3>
        {currency && !isCount && <span className="muted">{currency}</span>}
      </div>
      <div className="hbar-list" role="img" aria-label={title}>
        {items.map((it) => {
          const val = num(it.value);
          const pct = Math.min(100, (Math.abs(val) / maxVal) * 100);
          const fill =
            it.tone === "danger" || val < 0
              ? CHART_COLORS.crimson
              : it.tone === "warn"
                ? CHART_COLORS.copper
                : it.tone === "good"
                  ? CHART_COLORS.emerald
                  : barColor;
          return (
            <div className="hbar-row" key={it.label}>
              <div className="hbar-meta">
                <span className="hbar-label">{it.label}</span>
                <strong className="hbar-value">
                  {isCount
                    ? String(Math.round(val))
                    : money(String(val.toFixed(2)), currency || "USD")}
                  {it.sub ? ` (${it.sub})` : ""}
                </strong>
              </div>
              <div className="hbar-track">
                <div
                  className="hbar-fill"
                  style={{ width: `${pct}%`, background: fill }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CashBalanceTrendChart({
  title = "Cash flow & running balance trend",
  points,
  currency,
}: {
  title?: string;
  points: Array<{ label: string; balance: string }>;
  currency: string;
}) {
  if (!points.length) {
    return (
      <div className="visual-card">
        <h3>{title}</h3>
        <p className="visual-empty">No data for this period</p>
      </div>
    );
  }
  const vals = points.map((p) => num(p.balance));
  const minVal = Math.min(0, ...vals);
  const maxVal = Math.max(1, ...vals);
  const span = Math.max(1, maxVal - minVal);
  const hasDeficit = vals.some((v) => v < 0);
  const strokeColor = hasDeficit ? CHART_COLORS.crimson : CHART_COLORS.navy;

  const width = 540;
  const height = 200;
  const padL = 54;
  const padR = 18;
  const padT = 16;
  const padB = 32;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;

  const xAt = (idx: number) =>
    points.length === 1
      ? padL + plotW / 2
      : padL + (idx / (points.length - 1)) * plotW;
  const yAt = (v: number) => padT + ((maxVal - v) / span) * plotH;
  const zeroY = yAt(0);

  const linePath = points
    .map(
      (p, i) =>
        `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(num(p.balance)).toFixed(1)}`,
    )
    .join(" ");
  const areaPath =
    points.length > 1
      ? `${linePath} L${xAt(points.length - 1).toFixed(1)},${zeroY.toFixed(1)} L${xAt(0).toFixed(1)},${zeroY.toFixed(1)} Z`
      : "";

  return (
    <div className="visual-card">
      <div className="visual-card-head">
        <h3>{title}</h3>
        <Badge tone={hasDeficit ? "warning" : "good"}>
          {hasDeficit ? "Deficit detected" : "Positive trajectory"}
        </Badge>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="visual-svg"
        role="img"
        aria-label={title}
      >
        <line
          x1={padL}
          x2={width - padR}
          y1={zeroY}
          y2={zeroY}
          stroke={CHART_COLORS.crimson}
          strokeDasharray="3 3"
          strokeWidth="1"
        />
        {areaPath && <path d={areaPath} fill={strokeColor} opacity="0.14" />}
        {points.length > 1 && (
          <path
            d={linePath}
            fill="none"
            stroke={strokeColor}
            strokeWidth="2.4"
          />
        )}
        {points.map((pt, idx) => {
          const v = num(pt.balance);
          const showLbl =
            points.length <= 6 ||
            idx === 0 ||
            idx === points.length - 1 ||
            idx % Math.ceil(points.length / 5) === 0;
          return (
            <g key={`${pt.label}-${idx}`}>
              <circle
                cx={xAt(idx)}
                cy={yAt(v)}
                r="3.8"
                fill={v < 0 ? CHART_COLORS.crimson : CHART_COLORS.navy}
              >
                <title>{`${pt.label}: ${money(pt.balance, currency)}`}</title>
              </circle>
              {showLbl && (
                <text
                  x={xAt(idx)}
                  y={height - 8}
                  textAnchor="middle"
                  className="visual-axis-text"
                >
                  {pt.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function VisualExportBar({
  downloadPdfUrl,
  downloadHtmlUrl,
  onDownloadPdf,
  onDownloadHtml,
  downloadBusy,
  onSendEmail,
  emailBusy,
  emailNotice,
}: {
  downloadPdfUrl: string;
  downloadHtmlUrl: string;
  onDownloadPdf?: () => void;
  onDownloadHtml?: () => void;
  downloadBusy?: boolean;
  onSendEmail?: (recipientEmail: string) => void;
  emailBusy?: boolean;
  emailNotice?: string;
}) {
  const [emailAddr, setEmailAddr] = useState("");
  return (
    <div
      className="visual-export-bar"
      role="region"
      aria-label="Export visual report"
    >
      <div className="p4-inline-actions">
        {onDownloadPdf ? (
          <Button
            type="button"
            variant="secondary"
            busy={downloadBusy}
            onClick={onDownloadPdf}
          >
            Download PDF with charts
          </Button>
        ) : (
          <a className="button secondary" href={downloadPdfUrl} download>
            Download PDF with charts
          </a>
        )}
        {onDownloadHtml ? (
          <Button
            type="button"
            variant="secondary"
            busy={downloadBusy}
            onClick={onDownloadHtml}
          >
            Download HTML report
          </Button>
        ) : (
          <a className="button secondary" href={downloadHtmlUrl} download>
            Download HTML report
          </a>
        )}
      </div>
      {onSendEmail && (
        <form
          className="visual-email-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (emailAddr.trim()) {
              onSendEmail(emailAddr.trim());
            }
          }}
        >
          <Field label="Email report with charts">
            <input
              type="email"
              value={emailAddr}
              onChange={(e) => setEmailAddr(e.target.value)}
              placeholder="colleague@company.com"
              required
            />
          </Field>
          <Button type="submit" variant="secondary" busy={emailBusy}>
            Send visual report
          </Button>
        </form>
      )}
      {emailNotice && (
        <small className="success-text" role="status">
          {emailNotice}
        </small>
      )}
    </div>
  );
}
