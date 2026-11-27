"""Tests for Grafana provisioning, Image Renderer, SQL visual parity, and exports."""

from __future__ import annotations

import json
import os
import urllib.error
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from test_phase2_integration import (
    accountant_context,
    client,  # noqa: F401
    database_settings,  # noqa: F401
)

from ledgerpilot.config import Settings, get_settings
from ledgerpilot.local_setup import FileSettings
from ledgerpilot.reports import visuals

# ruff: noqa: F811 -- isolated real-service fixtures
REAL = pytest.mark.skipif(
    os.environ.get("RUN_PHASE2_INTEGRATION") != "1",
    reason="Requires local PostgreSQL/SMTP",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_provisioned_grafana_files_are_reproducible_readonly_and_secret_free() -> None:
    """Verify Compose, datasource YAML, dashboard YAML, SQL role script, and JSON dashboards."""
    grafana_dir = REPO_ROOT / "deploy" / "grafana"
    compose_path = REPO_ROOT / "docker-compose.grafana.yml"
    ds_path = grafana_dir / "provisioning" / "datasources" / "ledgerpilot-postgres.yml"
    dash_cfg_path = grafana_dir / "provisioning" / "dashboards" / "dashboards.yml"
    fin_dash_path = grafana_dir / "dashboards" / "ledgerpilot-financial-reports.json"
    aud_dash_path = grafana_dir / "dashboards" / "ledgerpilot-audit-reports.json"
    role_sql_path = grafana_dir / "bootstrap-readonly-role.sql"

    for p in (compose_path, ds_path, dash_cfg_path, fin_dash_path, aud_dash_path, role_sql_path):
        assert p.is_file(), f"Missing provisioned file: {p}"

    compose_raw = compose_path.read_text(encoding="utf-8")
    assert "grafana:" in compose_raw
    assert "grafana-renderer:" in compose_raw
    assert "grafana-db-init:" in compose_raw
    assert "ledgerpilot_grafana_ro" in compose_raw

    ds_raw = ds_path.read_text(encoding="utf-8")
    assert "${GRAFANA_DB_PASSWORD}" in ds_raw
    assert "uid: ledgerpilot-postgres-ro" in ds_raw
    assert "user: ${GRAFANA_DB_USER}" in ds_raw
    assert "editable: false" in ds_raw

    dash_cfg_raw = dash_cfg_path.read_text(encoding="utf-8")
    assert "allowUiUpdates: false" in dash_cfg_raw

    role_sql = role_sql_path.read_text(encoding="utf-8")
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT NOBYPASSRLS" in role_sql
    assert "default_transaction_read_only = on" in role_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA app" in role_sql
    assert "GRANT SELECT ON" in role_sql

    fin_dash = json.loads(fin_dash_path.read_text(encoding="utf-8"))
    assert fin_dash["uid"] == visuals.FINANCIAL_DASHBOARD_UID
    fin_vars = {v["name"] for v in fin_dash["templating"]["list"]}
    assert {"business_id", "period_mode", "account_kind", "currency"}.issubset(fin_vars)
    assert len(fin_dash["panels"]) == 10
    for panel in fin_dash["panels"]:
        assert panel["datasource"]["uid"] == "ledgerpilot-postgres-ro"
        for target in panel.get("targets", []):
            raw_sql = target.get("rawSql", "").upper()
            for mutating in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE "):
                assert mutating not in raw_sql

    aud_dash = json.loads(aud_dash_path.read_text(encoding="utf-8"))
    assert aud_dash["uid"] == visuals.AUDIT_DASHBOARD_UID
    aud_vars = {v["name"] for v in aud_dash["templating"]["list"]}
    assert {"business_id", "audit_type"}.issubset(aud_vars)
    assert len(aud_dash["panels"]) == 7


def test_grafana_image_renderer_client_caches_and_falls_back_cleanly() -> None:
    """Verify /render/d-solo/... URL construction, Bearer auth, caching, and offline fallback."""
    visuals._RENDER_CACHE.clear()
    biz_id = uuid4()
    fake_png = PNG_MAGIC + b"grafana-rendered-bytes"
    captured_requests: list[Any] = []

    class FakeResp:
        status = 200

        def __enter__(self) -> FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return fake_png

    def fake_urlopen(req: Any, timeout: float = 10.0) -> FakeResp:
        captured_requests.append((req, timeout))
        return FakeResp()

    settings = Settings(
        grafana_url="http://grafana.internal:3000",
        grafana_public_url="https://charts.example.com",
        grafana_api_token=SecretStr("glsa_test_token_123"),
        grafana_embed_enabled=True,
        grafana_render_cache_ttl_seconds=60,
    )

    sample_visual_data: dict[str, Any] = {
        "currency": "USD",
        "has_data": True,
        "income_expense_series": [
            {
                "period_label": "2026-10",
                "income": "4200.00",
                "expense": "1800.00",
                "net_profit": "2400.00",
                "running_cash": "2400.00",
            },
            {
                "period_label": "2026-11",
                "income": "5100.00",
                "expense": "2100.00",
                "net_profit": "3000.00",
                "running_cash": "5400.00",
            },
        ],
    }

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        png_1, source_1 = visuals.render_panel_png(
            settings,
            dashboard_uid=visuals.FINANCIAL_DASHBOARD_UID,
            panel_id=5,
            business_id=biz_id,
            business_version=3,
            start=date(2026, 10, 1),
            end=date(2026, 11, 30),
            extra_vars={"period_mode": "monthly", "currency": "USD"},
            fallback_visual_data=sample_visual_data,
            theme="light",
        )
        png_2, source_2 = visuals.render_panel_png(
            settings,
            dashboard_uid=visuals.FINANCIAL_DASHBOARD_UID,
            panel_id=5,
            business_id=biz_id,
            business_version=3,
            start=date(2026, 10, 1),
            end=date(2026, 11, 30),
            extra_vars={"period_mode": "monthly", "currency": "USD"},
            fallback_visual_data=sample_visual_data,
            theme="light",
        )

    assert source_1 == "grafana_renderer"
    assert source_2 == "grafana_renderer"
    assert png_1 == fake_png
    assert png_2 == fake_png
    assert len(captured_requests) == 1
    req_obj = captured_requests[0][0]
    assert "/render/d-solo/lp-financial-reports/ledgerpilot-financial-reports?" in req_obj.full_url
    assert f"var-business_id={biz_id}" in req_obj.full_url
    assert "var-period_mode=monthly" in req_obj.full_url
    assert req_obj.get_header("Authorization") == "Bearer glsa_test_token_123"

    embed_urls = visuals.build_grafana_embed_urls(
        settings,
        dashboard_uid=visuals.FINANCIAL_DASHBOARD_UID,
        slug=visuals.FINANCIAL_DASHBOARD_SLUG,
        business_id=biz_id,
        start=date(2026, 10, 1),
        end=date(2026, 11, 30),
        panel_ids=[5, 6, 9],
        extra_vars={"period_mode": "monthly"},
        theme="dark",
    )
    assert "5" in embed_urls and "9" in embed_urls
    assert embed_urls["5"].startswith(
        "https://charts.example.com/d-solo/lp-financial-reports/ledgerpilot-financial-reports?"
    )
    assert "theme=dark" in embed_urls["5"]

    visuals._RENDER_CACHE.clear()
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        configured, reachable, msg = visuals.probe_grafana_status(settings)
        assert configured is True
        assert reachable is False
        assert "unreachable" in msg.lower()

        fb_png, fb_source = visuals.render_panel_png(
            settings,
            dashboard_uid=visuals.FINANCIAL_DASHBOARD_UID,
            panel_id=5,
            business_id=biz_id,
            business_version=4,
            start=date(2026, 10, 1),
            end=date(2026, 11, 30),
            extra_vars={"period_mode": "monthly", "currency": "USD"},
            fallback_visual_data=sample_visual_data,
            theme="dark",
        )
    assert fb_source == "matplotlib_server_fallback"
    assert fb_png.startswith(PNG_MAGIC)
    assert len(fb_png) > 1500


@pytest.mark.parametrize(
    "chart_kind",
    [
        "income_expense_trend",
        "period_comparison",
        "category_breakdown",
        "cash_flow_trend",
        "balance_sheet_structure",
        "top_items",
        "audit_severity",
        "audit_categories",
        "audit_timeline",
    ],
)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_matplotlib_fallback_renders_all_populated_and_empty_states(
    chart_kind: str, theme: str
) -> None:
    """Every chart kind renders valid PNG bytes for both seeded data and empty periods."""
    empty_png = visuals.render_matplotlib_chart_png(
        chart_kind, {"currency": "USD", "has_data": False}, theme=theme
    )
    assert empty_png.startswith(PNG_MAGIC)

    populated = {
        "currency": "USD",
        "has_data": True,
        "income_expense_series": [
            {
                "period_label": "2026-09",
                "income": "3200.00",
                "expense": "3900.00",
                "net_profit": "-700.00",
                "running_cash": "-700.00",
            },
            {
                "period_label": "2026-10",
                "income": "6500.00",
                "expense": "2400.00",
                "net_profit": "4100.00",
                "running_cash": "3400.00",
            },
        ],
        "expense_categories": [
            {"label": "Cloud Infrastructure", "amount": "1400.00", "share_pct": "58.3"},
            {"label": "Contractor Services", "amount": "1000.00", "share_pct": "41.7"},
        ],
        "balance_sheet_sections": [
            {"section": "Assets", "amount": "12500.00"},
            {"section": "Liabilities", "amount": "4100.00"},
            {"section": "Equity", "amount": "8400.00"},
        ],
        "top_items": [
            {
                "entry_date": "2026-10-15",
                "label": "Acme Enterprise Retainer",
                "kind": "income",
                "amount": "6500.00",
            }
        ],
        "by_severity": [
            {"severity": "Critical", "count": 1},
            {"severity": "High", "count": 2},
            {"severity": "Medium", "count": 0},
            {"severity": "Low", "count": 1},
        ],
        "by_category": [
            {"category": "Bank Reconciliation", "count": 2},
            {"category": "Accounting Rules", "count": 2},
        ],
        "issues_over_time": [
            {
                "period_label": "2026-09-01 → 2026-09-30",
                "critical": 0,
                "high": 1,
                "medium": 1,
                "low": 0,
                "total": 2,
            },
            {
                "period_label": "2026-10-01 → 2026-10-31",
                "critical": 1,
                "high": 2,
                "medium": 0,
                "low": 1,
                "total": 4,
            },
        ],
    }
    pop_png = visuals.render_matplotlib_chart_png(chart_kind, populated, theme=theme)
    assert pop_png.startswith(PNG_MAGIC)
    assert len(pop_png) > len(empty_png) // 2


@REAL
@pytest.mark.anyio
async def test_live_sql_visuals_match_canonical_reports_exports_and_audit_runs(
    client: httpx.AsyncClient,
    database_settings: FileSettings,
) -> None:
    """Seed double-entry transactions + audits and verify SQL tie-out and PDF/HTML exports."""
    async with accountant_context(client, database_settings) as (bid, api):
        accountant_uid = (await api.get("/api/v1/auth/me")).json()["id"]

        acc_ids: dict[str, str] = {}
        for key, code, name, kind in [
            ("bank", "1000", "Operating bank", "asset"),
            ("sales", "4000", "Advisory revenue", "income"),
            ("expense", "5100", "Cloud hosting", "expense"),
        ]:
            created_acc = await api.post(
                f"/api/v1/businesses/{bid}/accounts",
                json={"code": code, "name": name, "kind": kind},
            )
            assert created_acc.status_code == 201, created_acc.text
            acc_ids[key] = str(created_acc.json()["id"])

        period_res = await api.post(
            f"/api/v1/businesses/{bid}/periods",
            json={"start_date": "2026-01-01", "end_date": "2026-12-31"},
        )
        assert period_res.status_code == 201, period_res.text

        for accounting_date, memo, dr_acc, cr_acc, amt in [
            (
                "2026-10-05",
                "October advisory retainer",
                acc_ids["bank"],
                acc_ids["sales"],
                "4500.00",
            ),
            (
                "2026-11-04",
                "November platform subscription",
                acc_ids["bank"],
                acc_ids["sales"],
                "6200.00",
            ),
            (
                "2026-11-08",
                "Cloud hosting & database cluster",
                acc_ids["expense"],
                acc_ids["bank"],
                "1750.00",
            ),
        ]:
            cur_ver = (await api.get(f"/api/v1/businesses/{bid}")).json()["version"]
            j_res = await api.post(
                f"/api/v1/businesses/{bid}/journals",
                json={
                    "accounting_date": accounting_date,
                    "memo": memo,
                    "currency": "USD",
                    "idempotency_key": uuid4().hex,
                    "expected_version": cur_ver,
                    "lines": [
                        {"account_id": dr_acc, "debit": amt},
                        {"account_id": cr_acc, "credit": amt},
                    ],
                },
            )
            assert j_res.status_code == 201, j_res.text

        pl = await api.get(
            f"/api/v1/businesses/{bid}/reports/profit-loss",
            params={"start": "2026-01-01", "end": "2026-11-30", "basis": "accrual"},
        )
        assert pl.status_code == 200, pl.text
        bs = await api.get(
            f"/api/v1/businesses/{bid}/reports/balance-sheet",
            params={"as_of": "2026-11-30", "basis": "accrual"},
        )
        assert bs.status_code == 200, bs.text

        vis_res = await api.get(
            f"/api/v1/businesses/{bid}/reports/visuals",
            params={
                "start": "2026-01-01",
                "end": "2026-11-30",
                "basis": "accrual",
                "period_mode": "yearly",
            },
        )
        assert vis_res.status_code == 200, vis_res.text
        vis_json = vis_res.json()
        assert Decimal(vis_json["kpis"]["total_income"]) == Decimal("10700.00")
        assert Decimal(vis_json["kpis"]["total_expense"]) == Decimal("1750.00")
        assert Decimal(vis_json["kpis"]["net_balance"]) == Decimal(pl.json()["net_profit"])
        assert len(vis_json["income_expense_series"]) >= 2

        assets_dec = Decimal(vis_json["kpis"]["total_assets"])
        liab_dec = Decimal(vis_json["kpis"]["total_liabilities"])
        eq_dec = Decimal(vis_json["kpis"]["total_equity"])
        assert assets_dec == liab_dec + eq_dec

        # Verify migration 0043 SQL functions return matching aggregations under RLS
        engine = create_engine(
            database_settings.database_url.get_secret_value(), hide_parameters=True
        )
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "SELECT set_config('app.user_id', :u, true), "
                        "set_config('app.business_id', :b, true)"
                    ),
                    {"u": str(accountant_uid), "b": str(bid)},
                )
                sql_kpi = (
                    conn.execute(
                        text("SELECT * FROM app.grafana_kpi_summary(:b, :s, :e)"),
                        {
                            "b": UUID(str(bid)),
                            "s": date(2026, 1, 1),
                            "e": date(2026, 11, 30),
                        },
                    )
                    .mappings()
                    .one()
                )
                assert Decimal(str(sql_kpi["total_income"])) == Decimal("10700.00")
                assert Decimal(str(sql_kpi["total_expense"])) == Decimal("1750.00")
        finally:
            engine.dispose()

        monthly_pdf = await api.get(
            f"/api/v1/businesses/{bid}/reports/visual-export",
            params={
                "report_type": "monthly-summary",
                "format": "pdf",
                "start": "2026-11-01",
                "end": "2026-11-30",
            },
        )
        assert monthly_pdf.status_code == 200
        assert monthly_pdf.headers["content-type"].startswith("application/pdf")
        assert monthly_pdf.content.startswith(b"%PDF-")

        yearly_pdf = await api.get(
            f"/api/v1/businesses/{bid}/reports/visual-export",
            params={
                "report_type": "yearly-summary",
                "format": "pdf",
                "start": "2026-01-01",
                "end": "2026-12-31",
            },
        )
        assert yearly_pdf.status_code == 200
        assert yearly_pdf.content.startswith(b"%PDF-")

        bs_html = await api.get(
            f"/api/v1/businesses/{bid}/reports/visual-export",
            params={
                "report_type": "balance-sheet",
                "format": "html",
                "as_of": "2026-11-30",
            },
        )
        assert bs_html.status_code == 200
        html_str = bs_html.text
        assert "data:image/png;base64," in html_str
        assert "1,750.00" in html_str

        audit_run = await api.post(
            f"/api/v1/businesses/{bid}/audits",
            json={"audit_type": "monthly", "month": "2026-11"},
        )
        assert audit_run.status_code == 201, audit_run.text
        audit_id = audit_run.json()["id"]

        aud_vis = await api.get(
            f"/api/v1/businesses/{bid}/audits/visuals",
            params={"audit_id": audit_id},
        )
        assert aud_vis.status_code == 200, aud_vis.text
        assert aud_vis.json()["has_data"] is True
        assert len(aud_vis.json()["by_severity"]) == 4

        aud_pdf = await api.get(
            f"/api/v1/businesses/{bid}/audits/{audit_id}/export",
            params={"format": "pdf"},
        )
        assert aud_pdf.status_code == 200
        assert aud_pdf.content.startswith(b"%PDF-")

        aud_html = await api.get(
            f"/api/v1/businesses/{bid}/audits/{audit_id}/export",
            params={"format": "html"},
        )
        assert aud_html.status_code == 200
        assert "data:image/png;base64," in aud_html.text

        with patch(
            "ledgerpilot.reports.visuals.render_panel_png",
            side_effect=RuntimeError("Simulated renderer crash"),
        ):
            degraded_html = await api.get(
                f"/api/v1/businesses/{bid}/reports/visual-export",
                params={
                    "report_type": "monthly-summary",
                    "format": "html",
                    "start": "2026-11-01",
                    "end": "2026-11-30",
                },
            )
        assert degraded_html.status_code == 200
        assert degraded_html.headers["x-visual-source"] == "table_only_degraded"
        assert "6,200.00" in degraded_html.text

        live_mail_settings = database_settings.model_copy(update={"auth_delivery_enabled": True})
        from ledgerpilot.main import create_app

        live_app = create_app(live_mail_settings)
        live_app.dependency_overrides[get_settings] = lambda: live_mail_settings
        async with (
            live_app.router.lifespan_context(live_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=live_app),
                base_url="https://api.ledgerpilot.test",
                cookies=api.cookies,
                headers=dict(api.headers),
            ) as live_client,
        ):
            with patch(
                "ledgerpilot.reports.router.deliver_workspace_email",
                return_value=(False, "failed", ["SMTP authentication failed"]),
            ):
                fail_email = await live_client.post(
                    f"/api/v1/businesses/{bid}/reports/email-visual",
                    json={
                        "recipient_email": "cfo@example.com",
                        "report_type": "monthly-summary",
                        "start": "2026-11-01",
                        "end": "2026-11-30",
                    },
                )
            assert fail_email.status_code == 502
            assert fail_email.json()["error"]["code"] == "ERR_EMAIL_DELIVERY_FAILED"

            with patch(
                "ledgerpilot.reports.router.deliver_workspace_email",
                return_value=(True, "brevo", []),
            ):
                ok_email = await live_client.post(
                    f"/api/v1/businesses/{bid}/reports/email-visual",
                    json={
                        "recipient_email": "cfo@example.com",
                        "report_type": "monthly-summary",
                        "start": "2026-11-01",
                        "end": "2026-11-30",
                    },
                )
            assert ok_email.status_code == 200
            assert ok_email.json()["status"] == "sent"
