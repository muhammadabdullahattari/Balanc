"""SQL-aggregated report visuals, Grafana Image Renderer client, and PDF/HTML visual exports."""

from __future__ import annotations

import base64
import html
import io
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from collections import OrderedDict
from datetime import UTC, date, datetime
from datetime import time as dt_time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Image as RLImage  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, TableStyle  # noqa: E402
from reportlab.platypus import Table as RLTable  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from ledgerpilot.audit.models import FinancialAuditRun  # noqa: E402
from ledgerpilot.config import Settings  # noqa: E402
from ledgerpilot.reports import service as report_service  # noqa: E402
from ledgerpilot.shared.errors import DomainError  # noqa: E402
from ledgerpilot.tenancy.service import get_business  # noqa: E402

logger = logging.getLogger("ledgerpilot.reports.visuals")

# Consistent, accessible LedgerPilot palette (matches provisioned Grafana dashboards)
PALETTE = {
    "navy": "#274b7a",
    "emerald": "#1f7a5c",
    "copper": "#b86b2b",
    "slate": "#4c638a",
    "crimson": "#b3353e",
    "muted": "#526079",
    "ink_light": "#172438",
    "ink_dark": "#f4f1eb",
    "bg_light": "#fefdfb",
    "bg_dark": "#1b2638",
    "grid_light": "#dce1e8",
    "grid_dark": "#3b4b65",
}

FINANCIAL_DASHBOARD_UID = "lp-financial-reports"
FINANCIAL_DASHBOARD_SLUG = "ledgerpilot-financial-reports"
AUDIT_DASHBOARD_UID = "lp-audit-reports"
AUDIT_DASHBOARD_SLUG = "ledgerpilot-audit-reports"

_RENDER_CACHE: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()
_RENDER_CACHE_MAX = 128


def _q2(val: Decimal | str | int | float) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"))


def _fmt_money(amount: Decimal | str, currency: str) -> str:
    dec = _q2(amount)
    sign = "-" if dec < 0 else ""
    return f"{sign}{currency} {abs(dec):,.2f}"


def _epoch_ms(day: date, *, end_of_day: bool = False) -> int:
    t = dt_time(23, 59, 59) if end_of_day else dt_time(0, 0, 0)
    return int(datetime.combine(day, t, tzinfo=UTC).timestamp() * 1000)


def probe_grafana_status(settings: Settings) -> tuple[bool, bool, str]:
    """Return (configured, reachable, human_status_message) without exposing credentials."""
    base_url = (settings.grafana_url or "").strip().rstrip("/")
    if not base_url:
        return (
            False,
            False,
            (
                "Grafana is not configured in this environment — displaying verified SQL charts "
                "rendered directly from your ledger (start docker-compose.grafana.yml and set "
                "GRAFANA_URL to enable live Grafana panels)."
            ),
        )
    req = urllib.request.Request(
        f"{base_url}/api/health",
        headers={"Accept": "application/json", "User-Agent": "LedgerPilot-Reports/1.0"},
    )
    token = (
        settings.grafana_api_token.get_secret_value().strip() if settings.grafana_api_token else ""
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if 200 <= resp.status < 300:
                return True, True, "Connected to provisioned Grafana reporting service."
    except Exception as exc:
        logger.warning("Grafana health check unreachable (%s): %s", base_url, exc)
    return (
        True,
        False,
        (
            "Grafana service is currently unreachable — automatically using verified "
            "server-rendered fallback charts from the same SQL figures."
        ),
    )


def build_grafana_embed_urls(
    settings: Settings,
    *,
    dashboard_uid: str,
    slug: str,
    business_id: UUID,
    start: date,
    end: date,
    panel_ids: list[int],
    extra_vars: dict[str, str],
    theme: str = "light",
) -> dict[str, str]:
    """Build iframe / d-solo URLs when GRAFANA_EMBED_ENABLED and GRAFANA_PUBLIC_URL are set."""
    public_base = (settings.grafana_public_url or settings.grafana_url or "").strip().rstrip("/")
    if not public_base or not settings.grafana_embed_enabled:
        return {}
    from_ms = _epoch_ms(start, end_of_day=False)
    to_ms = _epoch_ms(end, end_of_day=True)
    base_params: list[tuple[str, str]] = [
        ("orgId", "1"),
        ("from", str(from_ms)),
        ("to", str(to_ms)),
        ("theme", theme if theme in {"light", "dark"} else settings.grafana_default_theme),
        ("var-business_id", str(business_id)),
    ]
    for k, v in extra_vars.items():
        base_params.append((f"var-{k}", str(v)))
    urls: dict[str, str] = {}
    for pid in panel_ids:
        qs = urllib.parse.urlencode([*base_params, ("panelId", str(pid))])
        urls[str(pid)] = f"{public_base}/d-solo/{dashboard_uid}/{slug}?{qs}"
    return urls


def query_financial_visuals(
    db: Session,
    settings: Settings,
    business_id: UUID,
    start: date,
    end: date,
    basis: Literal["cash", "accrual"] = "accrual",
    period_mode: Literal["monthly", "yearly", "custom"] = "monthly",
) -> dict[str, Any]:
    """Aggregate chart datasets in SQL and tie out 100% to Profit & Loss and Balance Sheet."""
    if end < start:
        raise DomainError("ERR_DATE_RANGE", "Report end date must not precede start date.", 422)
    if (end - start).days > 3660:
        raise DomainError("ERR_DATE_RANGE", "Visual report span supports up to 10 years.", 422)

    business = get_business(db, business_id)

    # Canonical P&L and Balance Sheet ensure exact cash/accrual parity with report tables
    pl_report = report_service.financial(db, business_id, start, end, basis, "profit-loss")
    bs_report = report_service.financial(
        db, business_id, date(1900, 1, 1), end, basis, "balance-sheet"
    )

    total_income = _q2(pl_report["income"])
    total_expense = _q2(pl_report["expense"])
    net_balance = _q2(pl_report["net_profit"])
    retained_earnings = _q2(bs_report.get("retained_earnings", "0.00"))

    # Aggregate Balance Sheet sections from canonical balance-sheet items
    assets_total = Decimal("0.00")
    liabilities_total = Decimal("0.00")
    paid_equity_total = Decimal("0.00")
    cash_balance = Decimal("0.00")
    for item in bs_report["items"]:
        amt = _q2(item["amount"])
        if item["kind"] == "asset":
            assets_total += amt
            if any(
                kw in item["name"].lower()
                for kw in ("cash", "bank", "checking", "operating", "treasury")
            ):
                cash_balance += amt
        elif item["kind"] == "liability":
            liabilities_total += amt
        elif item["kind"] == "equity":
            paid_equity_total += amt
    if cash_balance == Decimal("0.00") and assets_total != Decimal("0.00"):
        # Sum non-control asset accounts directly in SQL when account names are custom
        raw_cash: Any = db.execute(
            text(
                """
                SELECT coalesce(sum(jl.debit - jl.credit), 0)
                FROM app.journals j
                JOIN app.journal_lines jl
                  ON jl.business_id = j.business_id AND jl.journal_id = j.id
                JOIN app.accounts a
                  ON a.business_id = jl.business_id AND a.id = jl.account_id
                WHERE j.business_id = :bid
                  AND j.status = 'posted'
                  AND j.accounting_date <= :end_dt
                  AND a.kind = 'asset'
                  AND a.control IS NULL
                """
            ),
            {"bid": business_id, "end_dt": end},
        ).scalar_one()
        cash_balance = _q2(raw_cash)

    total_equity = _q2(paid_equity_total + retained_earnings)

    # Category breakdowns (top 6 + "Other categories" so donuts/bars stay clean and readable)
    def _collapse_categories(kind_filter: str) -> list[dict[str, Any]]:
        positive = sorted(
            [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "label": f"{r['code']} · {r['name']}",
                    "amount_dec": _q2(r["amount"]),
                }
                for r in pl_report["items"]
                if r["kind"] == kind_filter and _q2(r["amount"]) > Decimal("0.00")
            ],
            key=lambda x: (-x["amount_dec"], x["code"]),
        )
        grand = sum((x["amount_dec"] for x in positive), Decimal("0.00"))
        if not positive or grand <= Decimal("0.00"):
            return []
        top = positive[:6]
        rest = positive[6:]
        out: list[dict[str, Any]] = []
        for entry in top:
            pct = ((entry["amount_dec"] / grand) * Decimal("100")).quantize(Decimal("0.01"))
            out.append(
                {
                    "code": entry["code"],
                    "name": entry["name"],
                    "label": entry["label"],
                    "amount": str(entry["amount_dec"]),
                    "share_pct": str(pct),
                }
            )
        if rest:
            other_amt = sum((x["amount_dec"] for x in rest), Decimal("0.00"))
            pct = ((other_amt / grand) * Decimal("100")).quantize(Decimal("0.01"))
            out.append(
                {
                    "code": "OTHER",
                    "name": "Other categories",
                    "label": "Other categories",
                    "amount": str(_q2(other_amt)),
                    "share_pct": str(pct),
                }
            )
        return out

    expense_categories = _collapse_categories("expense")
    income_categories = _collapse_categories("income")

    # SQL time-series aggregation (monthly or yearly buckets)
    granularity = "year" if period_mode == "yearly" or (end - start).days > 400 else "month"
    series_rows = (
        db.execute(
            text(
                f"""
                SELECT
                  date_trunc('{granularity}', j.accounting_date::timestamp)::date AS bucket_start,
                  coalesce(sum(
                    CASE WHEN a.kind = 'income' THEN jl.credit - jl.debit ELSE 0 END
                  ), 0) AS inc,
                  coalesce(sum(
                    CASE WHEN a.kind = 'expense' THEN jl.debit - jl.credit ELSE 0 END
                  ), 0) AS exp,
                  coalesce(sum(
                    CASE WHEN a.kind = 'asset' AND a.control IS NULL
                      THEN jl.debit - jl.credit ELSE 0 END
                  ), 0) AS cash_delta
                FROM app.journals j
                JOIN app.journal_lines jl
                  ON jl.business_id = j.business_id AND jl.journal_id = j.id
                JOIN app.accounts a
                  ON a.business_id = jl.business_id AND a.id = jl.account_id
                WHERE j.business_id = :bid
                  AND j.status = 'posted'
                  AND j.accounting_date BETWEEN :start_dt AND :end_dt
                GROUP BY 1
                ORDER BY 1 ASC
                """
            ),
            {"bid": business_id, "start_dt": start, "end_dt": end},
        )
        .mappings()
        .all()
    )

    # Opening cash before `start` so running_cash reflects true period cash balance trajectory
    opening_cash_raw: Any = db.execute(
        text(
            """
            SELECT coalesce(sum(jl.debit - jl.credit), 0)
            FROM app.journals j
            JOIN app.journal_lines jl
              ON jl.business_id = j.business_id AND jl.journal_id = j.id
            JOIN app.accounts a
              ON a.business_id = jl.business_id AND a.id = jl.account_id
            WHERE j.business_id = :bid
              AND j.status = 'posted'
              AND j.accounting_date < :start_dt
              AND a.kind = 'asset'
              AND a.control IS NULL
            """
        ),
        {"bid": business_id, "start_dt": start},
    ).scalar_one()
    running_cash = _q2(opening_cash_raw)

    income_expense_series: list[dict[str, str]] = []
    for row in series_rows:
        b_start: date = row["bucket_start"]
        if granularity == "year":
            b_end = min(end, date(b_start.year, 12, 31))
            label = f"{b_start.year}"
        else:
            last_day = monthrange(b_start.year, b_start.month)[1]
            b_end = min(end, date(b_start.year, b_start.month, last_day))
            label = f"{b_start.year:04d}-{b_start.month:02d}"
        inc = _q2(row["inc"])
        exp = _q2(row["exp"])
        cdelta = _q2(row["cash_delta"])
        running_cash = _q2(running_cash + cdelta)
        income_expense_series.append(
            {
                "period_label": label,
                "start_date": max(start, b_start).isoformat(),
                "end_date": b_end.isoformat(),
                "income": str(inc),
                "expense": str(exp),
                "net_profit": str(_q2(inc - exp)),
                "cash_delta": str(cdelta),
                "running_cash": str(running_cash),
            }
        )

    # Top accounts / transactions by amount in SQL
    top_rows = (
        db.execute(
            text(
                """
                SELECT
                  j.accounting_date AS entry_date,
                  coalesce(nullif(j.memo, ''), a.code || ' · ' || a.name) AS description,
                  a.code AS account_code,
                  a.name AS account_name,
                  a.kind AS account_kind,
                  greatest(jl.debit, jl.credit) AS amount
                FROM app.journals j
                JOIN app.journal_lines jl
                  ON jl.business_id = j.business_id AND jl.journal_id = j.id
                JOIN app.accounts a
                  ON a.business_id = jl.business_id AND a.id = jl.account_id
                WHERE j.business_id = :bid
                  AND j.status = 'posted'
                  AND j.accounting_date BETWEEN :start_dt AND :end_dt
                  AND a.control IS NULL
                ORDER BY greatest(jl.debit, jl.credit) DESC, j.accounting_date DESC
                LIMIT 8
                """
            ),
            {"bid": business_id, "start_dt": start, "end_dt": end},
        )
        .mappings()
        .all()
    )
    top_items = [
        {
            "entry_date": r["entry_date"].isoformat(),
            "label": str(r["description"])[:80],
            "account_code": str(r["account_code"]),
            "account_name": str(r["account_name"]),
            "kind": str(r["account_kind"]),
            "amount": str(_q2(r["amount"])),
        }
        for r in top_rows
        if _q2(r["amount"]) > Decimal("0.00")
    ]

    # Latest audit issue count for KPI stat row
    latest_audit = db.scalar(
        select(FinancialAuditRun)
        .where(
            FinancialAuditRun.business_id == business_id,
            FinancialAuditRun.period_end >= start,
            FinancialAuditRun.period_start <= end,
        )
        .order_by(FinancialAuditRun.created_at.desc())
        .limit(1)
    )
    audit_total = int((latest_audit.severity_summary or {}).get("total", 0)) if latest_audit else 0
    audit_crit = (
        int((latest_audit.severity_summary or {}).get("critical", 0)) if latest_audit else 0
    )

    balance_sheet_sections = [
        {"section": "Assets", "amount": str(assets_total)},
        {"section": "Liabilities", "amount": str(liabilities_total)},
        {"section": "Equity", "amount": str(total_equity)},
    ]

    has_data = bool(
        income_expense_series
        or assets_total != Decimal("0.00")
        or liabilities_total != Decimal("0.00")
        or total_equity != Decimal("0.00")
    )

    gf_configured, gf_reachable, gf_msg = probe_grafana_status(settings)
    embed_urls = build_grafana_embed_urls(
        settings,
        dashboard_uid=FINANCIAL_DASHBOARD_UID,
        slug=FINANCIAL_DASHBOARD_SLUG,
        business_id=business_id,
        start=start,
        end=end,
        panel_ids=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        extra_vars={
            "period_mode": period_mode,
            "account_kind": "expense",
            "currency": business.currency,
        },
        theme=settings.grafana_default_theme,
    )

    return {
        "business_id": str(business_id),
        "business_name": business.name,
        "currency": business.currency,
        "basis": basis,
        "period_mode": period_mode,
        "granularity": granularity,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "has_data": has_data,
        "kpis": {
            "total_income": str(total_income),
            "total_expense": str(total_expense),
            "net_balance": str(net_balance),
            "cash_balance": str(cash_balance),
            "total_assets": str(assets_total),
            "total_liabilities": str(liabilities_total),
            "total_equity": str(total_equity),
            "retained_earnings": str(retained_earnings),
            "balance_sheet_balanced": bool(bs_report.get("balanced", True)),
            "audit_issue_count": audit_total,
            "critical_issue_count": audit_crit,
        },
        "income_expense_series": income_expense_series,
        "expense_categories": expense_categories,
        "income_categories": income_categories,
        "balance_sheet_sections": balance_sheet_sections,
        "top_items": top_items,
        "grafana": {
            "configured": gf_configured,
            "reachable": gf_reachable,
            "embed_enabled": bool(embed_urls),
            "dashboard_uid": FINANCIAL_DASHBOARD_UID,
            "visual_source": "grafana_renderer" if gf_reachable else "matplotlib_server_fallback",
            "status_message": gf_msg,
            "embed_urls": embed_urls,
        },
    }


def query_audit_visuals(
    db: Session,
    settings: Settings,
    business_id: UUID,
    *,
    audit_id: UUID | None = None,
    audit_type: str = "all",
    limit_runs: int = 12,
) -> dict[str, Any]:
    """Aggregate audit severity, category, and historical timeline datasets in SQL."""
    business = get_business(db, business_id)
    stmt = select(FinancialAuditRun).where(FinancialAuditRun.business_id == business_id)
    if audit_type and audit_type != "all":
        stmt = stmt.where(FinancialAuditRun.audit_type == audit_type)
    runs = list(
        db.scalars(stmt.order_by(FinancialAuditRun.created_at.desc()).limit(max(1, limit_runs)))
    )

    target_run: FinancialAuditRun | None = None
    if audit_id is not None:
        candidate = db.get(FinancialAuditRun, audit_id)
        if candidate is not None and candidate.business_id == business_id:
            target_run = candidate
    if target_run is None and runs:
        target_run = runs[0]

    sev_map = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    cat_counts: dict[str, int] = {}
    health_status = "clean_verified"
    period_start = date.today().replace(day=1)
    period_end = date.today()

    if target_run is not None:
        raw_sev = target_run.severity_summary or {}
        sev_map["Critical"] = int(raw_sev.get("critical", 0))
        sev_map["High"] = int(raw_sev.get("high", 0))
        sev_map["Medium"] = int(raw_sev.get("medium", 0))
        sev_map["Low"] = int(raw_sev.get("low", 0))
        health_status = str(raw_sev.get("health_status", "clean_verified"))
        period_start = target_run.period_start
        period_end = target_run.period_end
        for finding in target_run.findings or []:
            raw_cat = str(finding.get("category") or "general").replace("_", " ").strip()
            cat_label = raw_cat.title() if raw_cat else "General"
            cat_counts[cat_label] = cat_counts.get(cat_label, 0) + 1

    by_severity = [{"severity": k, "count": v} for k, v in sev_map.items()]
    by_category = [
        {"category": k, "count": v}
        for k, v in sorted(cat_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    issues_over_time: list[dict[str, Any]] = []
    for r in reversed(runs):
        rs = r.severity_summary or {}
        issues_over_time.append(
            {
                "audit_id": str(r.id),
                "period_label": f"{r.period_start.isoformat()} → {r.period_end.isoformat()}",
                "audit_type": r.audit_type,
                "created_at": r.created_at.isoformat(),
                "critical": int(rs.get("critical", 0)),
                "high": int(rs.get("high", 0)),
                "medium": int(rs.get("medium", 0)),
                "low": int(rs.get("low", 0)),
                "total": int(rs.get("total", len(r.findings or []))),
            }
        )

    total_issues = sum(sev_map.values())
    gf_configured, gf_reachable, gf_msg = probe_grafana_status(settings)
    embed_urls = build_grafana_embed_urls(
        settings,
        dashboard_uid=AUDIT_DASHBOARD_UID,
        slug=AUDIT_DASHBOARD_SLUG,
        business_id=business_id,
        start=period_start,
        end=period_end,
        panel_ids=[1, 2, 3, 4, 5, 6, 7],
        extra_vars={"audit_type": audit_type or "all"},
        theme=settings.grafana_default_theme,
    )

    return {
        "business_id": str(business_id),
        "business_name": business.name,
        "selected_audit_id": str(target_run.id) if target_run else None,
        "audit_type_filter": audit_type or "all",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "has_data": target_run is not None,
        "kpis": {
            "total_issues": total_issues,
            "critical_issues": sev_map["Critical"],
            "high_issues": sev_map["High"],
            "medium_issues": sev_map["Medium"],
            "low_issues": sev_map["Low"],
            "runs_evaluated": len(runs),
            "health_status": health_status,
        },
        "by_severity": by_severity,
        "by_category": by_category,
        "issues_over_time": issues_over_time,
        "grafana": {
            "configured": gf_configured,
            "reachable": gf_reachable,
            "embed_enabled": bool(embed_urls),
            "dashboard_uid": AUDIT_DASHBOARD_UID,
            "visual_source": "grafana_renderer" if gf_reachable else "matplotlib_server_fallback",
            "status_message": gf_msg,
            "embed_urls": embed_urls,
        },
    }


def _style_axes(ax: Any, title: str, theme: str = "light") -> None:
    dark = theme == "dark"
    fg = PALETTE["ink_dark"] if dark else PALETTE["ink_light"]
    bg = PALETTE["bg_dark"] if dark else PALETTE["bg_light"]
    grid = PALETTE["grid_dark"] if dark else PALETTE["grid_light"]
    ax.set_facecolor(bg)
    ax.figure.set_facecolor(bg)
    ax.set_title(title, fontsize=11, fontweight="bold", color=fg, pad=10, loc="left")
    ax.tick_params(colors=fg, labelsize=8.5)
    for spine_name, spine in ax.spines.items():
        if spine_name in {"top", "right"}:
            spine.set_visible(False)
        else:
            spine.set_color(grid)
    ax.grid(axis="y", color=grid, linestyle="--", linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)


def render_matplotlib_chart_png(
    chart_kind: str,
    visual_data: dict[str, Any],
    *,
    theme: str = "light",
    width_px: int = 860,
    height_px: int = 360,
) -> bytes:
    """Server-side Matplotlib PNG renderer matching Grafana dashboard panels and thresholds."""
    dpi = 140
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    dark = theme == "dark"
    fg = PALETTE["ink_dark"] if dark else PALETTE["ink_light"]
    currency = str(visual_data.get("currency", "USD"))

    try:
        if chart_kind == "income_expense_trend":
            series = visual_data.get("income_expense_series") or []
            _style_axes(ax, f"Income vs. Expenses Over Time ({currency})", theme)
            if not series:
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [r["period_label"] for r in series]
                inc = [float(r["income"]) for r in series]
                exp = [float(r["expense"]) for r in series]
                net = [float(r["net_profit"]) for r in series]
                xs = list(range(len(labels)))
                if len(xs) == 1:
                    w = 0.25
                    ax.bar([xs[0] - w / 2], inc, width=w, color=PALETTE["emerald"], label="Income")
                    ax.bar([xs[0] + w / 2], exp, width=w, color=PALETTE["copper"], label="Expenses")
                else:
                    ax.plot(
                        xs, inc, marker="o", linewidth=2.2, color=PALETTE["emerald"], label="Income"
                    )
                    ax.fill_between(xs, inc, 0, color=PALETTE["emerald"], alpha=0.12)
                    ax.plot(
                        xs,
                        exp,
                        marker="o",
                        linewidth=2.2,
                        color=PALETTE["copper"],
                        label="Expenses",
                    )
                    ax.plot(
                        xs,
                        net,
                        marker="s",
                        linestyle="--",
                        linewidth=1.8,
                        color=PALETTE["navy"],
                        label="Net Profit",
                    )
                ax.axhline(0, color=PALETTE["muted"], linewidth=0.8)
                ax.set_xticks(xs)
                ax.set_xticklabels(labels, rotation=25 if len(labels) > 6 else 0)
                ax.legend(frameon=False, fontsize=8.5, labelcolor=fg)

        elif chart_kind == "period_comparison":
            series = visual_data.get("income_expense_series") or []
            _style_axes(ax, f"Period Income & Expense Comparison ({currency})", theme)
            if not series:
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [r["period_label"] for r in series]
                inc = [float(r["income"]) for r in series]
                exp = [float(r["expense"]) for r in series]
                xs = list(range(len(labels)))
                w = 0.36
                ax.bar(
                    [x - w / 2 for x in xs],
                    inc,
                    width=w,
                    color=PALETTE["emerald"],
                    label="Income",
                )
                ax.bar(
                    [x + w / 2 for x in xs], exp, width=w, color=PALETTE["copper"], label="Expenses"
                )
                ax.set_ylim(bottom=0)
                ax.set_xticks(xs)
                ax.set_xticklabels(labels, rotation=25 if len(labels) > 6 else 0)
                ax.legend(frameon=False, fontsize=8.5, labelcolor=fg)

        elif chart_kind == "category_breakdown":
            cats = (
                visual_data.get("expense_categories") or visual_data.get("income_categories") or []
            )
            kind_lbl = "Expense" if visual_data.get("expense_categories") else "Income"
            _style_axes(ax, f"{kind_lbl} Breakdown by Category ({currency})", theme)
            if not cats:
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [c["label"][:34] for c in reversed(cats)]
                vals = [float(c["amount"]) for c in reversed(cats)]
                ax.barh(labels, vals, color=PALETTE["navy"], height=0.6)
                ax.set_xlim(left=0)
                ax.grid(axis="x", color=PALETTE["grid_light"], linestyle="--", linewidth=0.6)
                ax.grid(axis="y", visible=False)

        elif chart_kind == "cash_flow_trend":
            series = visual_data.get("income_expense_series") or []
            _style_axes(ax, f"Cash Flow & Running Balance Trend ({currency})", theme)
            if not series:
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [r["period_label"] for r in series]
                rcash = [float(r["running_cash"]) for r in series]
                xs = list(range(len(labels)))
                line_color = PALETTE["crimson"] if min(rcash) < 0 else PALETTE["navy"]
                ax.plot(
                    xs, rcash, marker="o", linewidth=2.3, color=line_color, label="Running Cash"
                )
                ax.fill_between(xs, rcash, 0, color=line_color, alpha=0.14)
                ax.axhline(0, color=PALETTE["crimson"], linestyle=":", linewidth=1.0)
                ax.set_xticks(xs)
                ax.set_xticklabels(labels, rotation=25 if len(labels) > 6 else 0)
                ax.legend(frameon=False, fontsize=8.5, labelcolor=fg)

        elif chart_kind == "balance_sheet_structure":
            sections = visual_data.get("balance_sheet_sections") or []
            _style_axes(ax, f"Balance Sheet: Assets vs. Liabilities vs. Equity ({currency})", theme)
            vals = [float(s["amount"]) for s in sections]
            if not sections or all(abs(v) < 0.005 for v in vals):
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [s["section"] for s in sections]
                bar_colors = [
                    PALETTE["navy"],
                    PALETTE["copper"],
                    PALETTE["crimson"] if vals[-1] < 0 else PALETTE["emerald"],
                ]
                ax.bar(labels, vals, color=bar_colors[: len(labels)], width=0.5)
                ax.axhline(0, color=PALETTE["muted"], linewidth=0.8)

        elif chart_kind == "top_items":
            items = visual_data.get("top_items") or []
            _style_axes(ax, f"Top Accounts & Entries by Amount ({currency})", theme)
            if not items:
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [f"{r['entry_date']} · {r['label'][:26]}" for r in reversed(items)]
                vals = [float(r["amount"]) for r in reversed(items)]
                ax.barh(labels, vals, color=PALETTE["slate"], height=0.6)
                ax.set_xlim(left=0)

        elif chart_kind == "audit_severity":
            sev = visual_data.get("by_severity") or []
            _style_axes(ax, "Audit Findings by Severity", theme)
            if not visual_data.get("has_data"):
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [s["severity"] for s in sev]
                vals = [int(s["count"]) for s in sev]
                sev_colors = [
                    PALETTE["crimson"],
                    PALETTE["copper"],
                    PALETTE["slate"],
                    PALETTE["emerald"],
                ]
                ax.bar(labels, vals, color=sev_colors[: len(labels)], width=0.5)
                ax.set_ylim(bottom=0, top=max(max(vals, default=0) + 1, 2))

        elif chart_kind == "audit_categories":
            cats = visual_data.get("by_category") or []
            _style_axes(ax, "Audit Findings by Category", theme)
            if not cats:
                ax.text(
                    0.5,
                    0.5,
                    "Zero findings in selected audit period",
                    ha="center",
                    va="center",
                    color=fg,
                )
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [c["category"] for c in reversed(cats)]
                vals = [int(c["count"]) for c in reversed(cats)]
                ax.barh(labels, vals, color=PALETTE["navy"], height=0.55)
                ax.set_xlim(left=0, right=max(max(vals, default=0) + 1, 2))

        else:  # audit_timeline
            timeline = visual_data.get("issues_over_time") or []
            _style_axes(ax, "Audit Findings Over Time", theme)
            if not timeline:
                ax.text(0.5, 0.5, "No data for this period", ha="center", va="center", color=fg)
                ax.set_xticks([])
                ax.set_yticks([])
            else:
                labels = [t["period_label"][:18] for t in timeline]
                crit = [int(t["critical"]) for t in timeline]
                high = [int(t["high"]) for t in timeline]
                med_low = [int(t["medium"]) + int(t["low"]) for t in timeline]
                xs = list(range(len(labels)))
                ax.bar(xs, crit, color=PALETTE["crimson"], label="Critical", width=0.52)
                ax.bar(xs, high, bottom=crit, color=PALETTE["copper"], label="High", width=0.52)
                bottom_ml = [c + h for c, h in zip(crit, high, strict=True)]
                ax.bar(
                    xs,
                    med_low,
                    bottom=bottom_ml,
                    color=PALETTE["slate"],
                    label="Medium / Low",
                    width=0.52,
                )
                ax.set_ylim(bottom=0)
                ax.set_xticks(xs)
                ax.set_xticklabels(labels, rotation=20 if len(labels) > 4 else 0)
                ax.legend(frameon=False, fontsize=8.5, labelcolor=fg)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
        return buf.getvalue()
    finally:
        plt.close(fig)


_PANEL_MAP_FINANCIAL: dict[int, str] = {
    5: "income_expense_trend",
    6: "category_breakdown",
    7: "period_comparison",
    8: "cash_flow_trend",
    9: "balance_sheet_structure",
    10: "top_items",
}

_PANEL_MAP_AUDIT: dict[int, str] = {
    5: "audit_severity",
    6: "audit_categories",
    7: "audit_timeline",
}


def render_panel_png(
    settings: Settings,
    *,
    dashboard_uid: str,
    panel_id: int,
    business_id: UUID,
    business_version: int,
    start: date,
    end: date,
    extra_vars: dict[str, str],
    fallback_visual_data: dict[str, Any],
    theme: Literal["light", "dark"] = "light",
    width: int = 860,
    height: int = 360,
) -> tuple[bytes, str]:
    """Render panel via Grafana (/render/d-solo/...), falling back to Matplotlib PNG."""
    slug = (
        AUDIT_DASHBOARD_SLUG if dashboard_uid == AUDIT_DASHBOARD_UID else FINANCIAL_DASHBOARD_SLUG
    )
    var_part = "|".join(f"{k}={v}" for k, v in sorted(extra_vars.items()))
    cache_key = (
        f"{business_id}:v{business_version}:{dashboard_uid}:p{panel_id}:"
        f"{start}:{end}:{theme}:{width}x{height}:{var_part}"
    )
    now_ts = time.monotonic()
    ttl = max(0, settings.grafana_render_cache_ttl_seconds)
    if ttl > 0 and cache_key in _RENDER_CACHE:
        exp_ts, cached_bytes, cached_source = _RENDER_CACHE[cache_key]
        if now_ts < exp_ts:
            _RENDER_CACHE.move_to_end(cache_key)
            return cached_bytes, cached_source

    base_url = (settings.grafana_url or "").strip().rstrip("/")
    if base_url:
        params: list[tuple[str, str]] = [
            ("orgId", "1"),
            ("panelId", str(panel_id)),
            ("from", str(_epoch_ms(start, end_of_day=False))),
            ("to", str(_epoch_ms(end, end_of_day=True))),
            ("var-business_id", str(business_id)),
            ("theme", theme),
            ("width", str(width)),
            ("height", str(height)),
            ("tz", "UTC"),
        ]
        for k, v in extra_vars.items():
            params.append((f"var-{k}", str(v)))
        render_url = (
            f"{base_url}/render/d-solo/{dashboard_uid}/{slug}?{urllib.parse.urlencode(params)}"
        )
        req = urllib.request.Request(
            render_url,
            headers={"Accept": "image/png", "User-Agent": "LedgerPilot-Reports/1.0"},
        )
        token = (
            settings.grafana_api_token.get_secret_value().strip()
            if settings.grafana_api_token
            else ""
        )
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(
                req, timeout=float(settings.grafana_render_timeout_seconds)
            ) as resp:
                body = resp.read()
                if 200 <= resp.status < 300 and body.startswith(b"\x89PNG"):
                    if ttl > 0:
                        _RENDER_CACHE[cache_key] = (now_ts + ttl, body, "grafana_renderer")
                        while len(_RENDER_CACHE) > _RENDER_CACHE_MAX:
                            _RENDER_CACHE.popitem(last=False)
                    return body, "grafana_renderer"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "Grafana renderer unavailable for %s panel %d (%s); using server PNG fallback.",
                dashboard_uid,
                panel_id,
                exc,
            )

    chart_kind = (
        _PANEL_MAP_AUDIT.get(panel_id, "audit_severity")
        if dashboard_uid == AUDIT_DASHBOARD_UID
        else _PANEL_MAP_FINANCIAL.get(panel_id, "income_expense_trend")
    )
    png = render_matplotlib_chart_png(
        chart_kind, fallback_visual_data, theme=theme, width_px=width, height_px=height
    )
    if ttl > 0:
        _RENDER_CACHE[cache_key] = (now_ts + ttl, png, "matplotlib_server_fallback")
        while len(_RENDER_CACHE) > _RENDER_CACHE_MAX:
            _RENDER_CACHE.popitem(last=False)
    return png, "matplotlib_server_fallback"


def build_financial_visual_export(
    db: Session,
    settings: Settings,
    business_id: UUID,
    *,
    report_type: str,
    start: date,
    end: date,
    basis: Literal["cash", "accrual"] = "accrual",
    fmt: Literal["pdf", "html"] = "pdf",
    theme: Literal["light", "dark"] = "light",
) -> dict[str, Any]:
    """Generate a PDF or HTML financial report pairing rendered charts with verifiable tables."""
    period_mode: Literal["monthly", "yearly", "custom"] = (
        "yearly"
        if report_type == "yearly-summary"
        else ("monthly" if report_type == "monthly-summary" else "custom")
    )
    business = get_business(db, business_id)
    visuals = query_financial_visuals(
        db, settings, business_id, start, end, basis=basis, period_mode=period_mode
    )
    pl_data = report_service.financial(db, business_id, start, end, basis, "profit-loss")
    bs_data = report_service.financial(
        db, business_id, date(1900, 1, 1), end, basis, "balance-sheet"
    )

    panel_list = (
        [9, 5]
        if report_type == "balance-sheet"
        else ([5, 6, 8, 9] if report_type in {"monthly-summary", "yearly-summary"} else [5, 6, 7])
    )
    rendered_panels: list[tuple[str, bytes, str]] = []
    source_used = "table_only_degraded"
    for pid in panel_list:
        try:
            png, src = render_panel_png(
                settings,
                dashboard_uid=FINANCIAL_DASHBOARD_UID,
                panel_id=pid,
                business_id=business_id,
                business_version=business.version,
                start=start,
                end=end,
                extra_vars={
                    "period_mode": period_mode,
                    "account_kind": "expense",
                    "currency": business.currency,
                },
                fallback_visual_data=visuals,
                theme=theme,
            )
            rendered_panels.append((_PANEL_MAP_FINANCIAL.get(pid, f"panel_{pid}"), png, src))
            source_used = src
        except Exception as exc:
            logger.warning("Chart panel %d skipped during export (degrading cleanly): %s", pid, exc)

    title_map = {
        "balance-sheet": f"Balance Sheet Report (as of {end.isoformat()})",
        "profit-loss": f"Profit & Loss Report ({start.isoformat()} to {end.isoformat()})",
        "monthly-summary": f"Monthly Financial Report ({start.isoformat()} to {end.isoformat()})",
        "yearly-summary": f"Yearly Financial Summary ({start.isoformat()} to {end.isoformat()})",
    }
    report_title = title_map.get(
        report_type, f"Financial Report ({start.isoformat()} to {end.isoformat()})"
    )
    currency = business.currency
    kpis = visuals["kpis"]

    active_table_items = bs_data["items"] if report_type == "balance-sheet" else pl_data["items"]

    if fmt == "html":
        imgs_html = "".join(
            f'<div class="chart-box"><img alt="{html.escape(name)}" '
            f'src="data:image/png;base64,{base64.b64encode(png).decode("ascii")}" /></div>'
            for name, png, _ in rendered_panels
        )
        rows_html = (
            "".join(
                f"<tr><td>{html.escape(r['code'])}</td>"
                f"<td>{html.escape(r['name'])}</td>"
                f"<td>{html.escape(r['kind'].title())}</td>"
                f"<td style='text-align:right'>"
                f"{html.escape(_fmt_money(r['amount'], currency))}</td></tr>"
                for r in active_table_items
            )
            or '<tr><td colspan="4">No posted entries for this period.</td></tr>'
        )
        source_badge = (
            "Grafana Image Renderer (/render/d-solo)"
            if source_used == "grafana_renderer"
            else (
                "Server-rendered chart fallback (Grafana offline)"
                if rendered_panels
                else "Table-only fallback (chart layer unavailable)"
            )
        )
        inc_str = html.escape(_fmt_money(kpis["total_income"], currency))
        exp_str = html.escape(_fmt_money(kpis["total_expense"], currency))
        net_str = html.escape(_fmt_money(kpis["net_balance"], currency))
        ast_str = html.escape(_fmt_money(kpis["total_assets"], currency))
        doc_html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<title>{html.escape(business.name)} — {html.escape(report_title)}</title>
<style>
  body {{
    font-family: Segoe UI, system-ui, sans-serif; color: #172438;
    margin: 32px; background: #fefdfb;
  }}
  h1 {{ margin: 0 0 4px; color: #274b7a; font-size: 22px; }}
  .meta {{ color: #526079; font-size: 13px; margin-bottom: 20px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }}
  .kpi {{ border: 1px solid #dce1e8; border-radius: 10px; padding: 12px 14px; background: #fff; }}
  .kpi small {{ display: block; color: #526079; font-size: 11px; text-transform: uppercase; }}
  .kpi strong {{ font-size: 17px; }}
  .charts {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 16px; margin-bottom: 24px;
  }}
  .chart-box img {{ width: 100%; border: 1px solid #dce1e8; border-radius: 10px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #dce1e8; padding: 8px 10px; text-align: left; }}
  th {{ background: #e9edf2; font-weight: 600; }}
</style></head><body>
  <h1>{html.escape(business.name)} — {html.escape(report_title)}</h1>
  <div class="meta">
    Basis: {html.escape(basis.title())} · Currency: {html.escape(currency)}
    · Visual engine: {html.escape(source_badge)}
  </div>
  <div class="kpis">
    <div class="kpi"><small>Total Income</small><strong>{inc_str}</strong></div>
    <div class="kpi"><small>Total Expenses</small><strong>{exp_str}</strong></div>
    <div class="kpi"><small>Net Profit / Balance</small><strong>{net_str}</strong></div>
    <div class="kpi"><small>Total Assets</small><strong>{ast_str}</strong></div>
  </div>
  <div class="charts">{imgs_html}</div>
  <h2>Supporting Ledger Figures</h2>
  <table><thead><tr>
    <th>Code</th><th>Account</th><th>Classification</th>
    <th style="text-align:right">Amount</th>
  </tr></thead>
  <tbody>{rows_html}</tbody></table>
</body></html>"""
        raw_bytes = doc_html.encode("utf-8")
        return {
            "bytes": raw_bytes,
            "content_type": "text/html; charset=utf-8",
            "filename": f"{report_type}-{end.isoformat()}.html",
            "visual_source": source_used,
        }

    # Build multi-page PDF via ReportLab with embedded PNGs + supporting accounting table
    pdf_buf = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{business.name} - {report_title}",
    )
    styles = getSampleStyleSheet()
    h1_style = ParagraphStyle(
        "LPTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.HexColor(PALETTE["navy"]),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "LPMeta",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor(PALETTE["muted"]),
        spaceAfter=12,
    )
    story: list[Any] = [
        Paragraph(f"{html.escape(business.name)} — {html.escape(report_title)}", h1_style),
        Paragraph(
            f"Basis: {basis.title()} | Currency: {currency} | Visual source: {source_used}",
            meta_style,
        ),
    ]

    kpi_rows = [
        ["Total Income", "Total Expenses", "Net Profit / Balance", "Total Assets"],
        [
            _fmt_money(kpis["total_income"], currency),
            _fmt_money(kpis["total_expense"], currency),
            _fmt_money(kpis["net_balance"], currency),
            _fmt_money(kpis["total_assets"], currency),
        ],
    ]
    kpi_tbl = RLTable(kpi_rows, colWidths=[44 * mm, 44 * mm, 44 * mm, 44 * mm])
    kpi_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(PALETTE["muted"])),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 1), (-1, 1), 10),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dce1e8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dce1e8")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(kpi_tbl)
    story.append(Spacer(1, 10))

    for _name, png_bytes, _src in rendered_panels:
        story.append(RLImage(io.BytesIO(png_bytes), width=174 * mm, height=72 * mm))
        story.append(Spacer(1, 8))

    story.append(Paragraph("Supporting Account Balances", styles["Heading2"]))
    tbl_rows: list[list[str]] = [["Code", "Account", "Classification", "Amount"]]
    for r in active_table_items:
        tbl_rows.append(
            [r["code"], r["name"][:44], r["kind"].title(), _fmt_money(r["amount"], currency)]
        )
    if len(tbl_rows) == 1:
        tbl_rows.append(
            ["—", "No posted entries for this period", "—", _fmt_money("0.00", currency)]
        )
    if report_type == "balance-sheet":
        tbl_rows.append(
            ["—", "Retained Earnings", "Equity", _fmt_money(kpis["retained_earnings"], currency)]
        )
    else:
        tbl_rows.append(["—", "Net Profit", "Summary", _fmt_money(kpis["net_balance"], currency)])

    ledger_tbl = RLTable(tbl_rows, colWidths=[24 * mm, 80 * mm, 32 * mm, 40 * mm], repeatRows=1)
    ledger_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(PALETTE["navy"])),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8fa")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce1e8")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(ledger_tbl)
    doc.build(story)
    return {
        "bytes": pdf_buf.getvalue(),
        "content_type": "application/pdf",
        "filename": f"{report_type}-{end.isoformat()}.pdf",
        "visual_source": source_used,
    }


def build_audit_visual_export(
    db: Session,
    settings: Settings,
    business_id: UUID,
    audit_id: UUID,
    *,
    fmt: Literal["pdf", "html"] = "pdf",
    theme: Literal["light", "dark"] = "light",
) -> dict[str, Any]:
    """Generate a PDF or HTML Financial Audit report with charts + findings."""
    row = db.get(FinancialAuditRun, audit_id)
    if row is None or row.business_id != business_id:
        raise DomainError("ERR_NOT_FOUND", "Financial audit report not found.", 404)
    business = get_business(db, business_id)
    visuals = query_audit_visuals(db, settings, business_id, audit_id=audit_id)

    rendered_panels: list[tuple[str, bytes, str]] = []
    source_used = "table_only_degraded"
    for pid in (5, 6, 7):
        try:
            png, src = render_panel_png(
                settings,
                dashboard_uid=AUDIT_DASHBOARD_UID,
                panel_id=pid,
                business_id=business_id,
                business_version=business.version,
                start=row.period_start,
                end=row.period_end,
                extra_vars={"audit_type": row.audit_type},
                fallback_visual_data=visuals,
                theme=theme,
            )
            rendered_panels.append((_PANEL_MAP_AUDIT.get(pid, f"panel_{pid}"), png, src))
            source_used = src
        except Exception as exc:
            logger.warning("Audit chart panel %d skipped during export: %s", pid, exc)

    kpis = visuals["kpis"]
    summary_text = str(
        (row.polished_report or {}).get("executive_summary")
        or "Financial audit verification completed."
    )
    title = f"Financial Audit ({row.audit_type.title()}: {row.period_start} to {row.period_end})"

    if fmt == "html":
        imgs_html = "".join(
            f'<div class="chart-box"><img alt="{html.escape(name)}" '
            f'src="data:image/png;base64,{base64.b64encode(png).decode("ascii")}" /></div>'
            for name, png, _ in rendered_panels
        )
        findings_rows = (
            "".join(
                f"<tr><td>{html.escape(str(f.get('severity', '')))}</td>"
                f"<td>{html.escape(str(f.get('category', '')).replace('_', ' ').title())}</td>"
                f"<td>{html.escape(str(f.get('rule_violated', '')).replace('_', ' '))}</td>"
                f"<td>{html.escape(str(f.get('recommendation', '')))}</td></tr>"
                for f in (row.findings or [])
            )
            or '<tr><td colspan="4">Zero accounting discrepancies detected.</td></tr>'
        )
        html_doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<title>{html.escape(business.name)} — {html.escape(title)}</title>
<style>
  body {{
    font-family: Segoe UI, system-ui, sans-serif; color: #172438;
    margin: 32px; background: #fefdfb;
  }}
  h1 {{ margin: 0 0 6px; color: #274b7a; font-size: 22px; }}
  .meta {{ color: #526079; font-size: 13px; margin-bottom: 18px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }}
  .kpi {{ border: 1px solid #dce1e8; border-radius: 10px; padding: 12px; background: #fff; }}
  .charts {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
    gap: 16px; margin: 18px 0;
  }}
  .chart-box img {{ width: 100%; border: 1px solid #dce1e8; border-radius: 10px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{
    border-bottom: 1px solid #dce1e8; padding: 8px 10px;
    text-align: left; vertical-align: top;
  }}
  th {{ background: #e9edf2; }}
</style></head><body>
  <h1>{html.escape(business.name)} — {html.escape(title)}</h1>
  <div class="meta">
    Visual source: {html.escape(source_used)} · Total findings: {kpis["total_issues"]}
  </div>
  <p>{html.escape(summary_text)}</p>
  <div class="kpis">
    <div class="kpi"><small>Critical</small><br/><strong>{kpis["critical_issues"]}</strong></div>
    <div class="kpi"><small>High</small><br/><strong>{kpis["high_issues"]}</strong></div>
    <div class="kpi"><small>Medium</small><br/><strong>{kpis["medium_issues"]}</strong></div>
    <div class="kpi"><small>Low</small><br/><strong>{kpis["low_issues"]}</strong></div>
  </div>
  <div class="charts">{imgs_html}</div>
  <h2>Supporting Audit Findings</h2>
  <table><thead><tr><th>Severity</th><th>Category</th><th>Finding</th><th>Recommendation</th></tr></thead>
  <tbody>{findings_rows}</tbody></table>
</body></html>"""
        return {
            "bytes": html_doc.encode("utf-8"),
            "content_type": "text/html; charset=utf-8",
            "filename": f"audit-{row.audit_type}-{row.period_end.isoformat()}.html",
            "visual_source": source_used,
        }

    pdf_buf = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{business.name} - {title}",
    )
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph(f"{html.escape(business.name)} — {html.escape(title)}", styles["Heading1"]),
        Paragraph(html.escape(summary_text), styles["Normal"]),
        Spacer(1, 8),
    ]
    kpi_tbl = RLTable(
        [
            ["Critical", "High", "Medium", "Low"],
            [
                str(kpis["critical_issues"]),
                str(kpis["high_issues"]),
                str(kpis["medium_issues"]),
                str(kpis["low_issues"]),
            ],
        ],
        colWidths=[44 * mm, 44 * mm, 44 * mm, 44 * mm],
    )
    kpi_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf2")),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dce1e8")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(kpi_tbl)
    story.append(Spacer(1, 10))

    for _name, png_bytes, _src in rendered_panels:
        story.append(RLImage(io.BytesIO(png_bytes), width=174 * mm, height=70 * mm))
        story.append(Spacer(1, 8))

    story.append(Paragraph("Supporting Audit Findings", styles["Heading2"]))
    f_rows: list[list[Any]] = [["Severity", "Category", "Rule", "Recommendation"]]
    for f in row.findings or []:
        f_rows.append(
            [
                str(f.get("severity", "")),
                str(f.get("category", "")).replace("_", " ").title(),
                Paragraph(html.escape(str(f.get("rule_violated", ""))[:60]), styles["Normal"]),
                Paragraph(html.escape(str(f.get("recommendation", ""))[:140]), styles["Normal"]),
            ]
        )
    if len(f_rows) == 1:
        f_rows.append(["Clean", "All Rules", "Verified", "Zero accounting discrepancies found."])
    f_tbl = RLTable(f_rows, colWidths=[22 * mm, 30 * mm, 48 * mm, 76 * mm], repeatRows=1)
    f_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(PALETTE["navy"])),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce1e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(f_tbl)
    doc.build(story)
    return {
        "bytes": pdf_buf.getvalue(),
        "content_type": "application/pdf",
        "filename": f"audit-{row.audit_type}-{row.period_end.isoformat()}.pdf",
        "visual_source": source_used,
    }
