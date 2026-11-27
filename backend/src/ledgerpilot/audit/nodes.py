"""Single-responsibility LangGraph nodes for the financial audit workflow."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ledgerpilot.ai.adapter import GroqModelAdapter
from ledgerpilot.audit.loader import load_audit_dataset, resolve_earliest_record_date
from ledgerpilot.audit.models import FinancialAuditRun
from ledgerpilot.audit.polisher import build_templated_report, polish_with_groq
from ledgerpilot.audit.service import record as record_audit_event
from ledgerpilot.audit.state import DATASET_REGISTRY, AuditState
from ledgerpilot.audit.tools import (
    detect_ledger_and_bank_anomalies_tool,
    evaluate_accounting_rules_tool,
    evaluate_banking_rules_tool,
    evaluate_payment_and_close_rules_tool,
    reconcile_balances_and_continuity_tool,
    retrieve_audit_context_tool,
)
from ledgerpilot.config import Settings
from ledgerpilot.tenancy.service import get_business

_SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


@dataclass
class AuditRuntimeDeps:
    """Per-invocation dependencies injected via closure (keeps AuditState JSON-serializable)."""

    db: Session
    settings: Settings
    adapter: GroqModelAdapter | None = None
    persist: bool = True


def _normalize_period(
    db: Session,
    business_id: UUID,
    audit_type: str,
    params: dict[str, Any],
) -> tuple[date, date, str, int]:
    business = get_business(db, business_id)
    tz = ZoneInfo(business.timezone)
    today_dt = datetime.now(UTC).astimezone(tz).date()
    fiscal_month = int(business.fiscal_month or 1)

    if audit_type == "monthly":
        month_raw = str(params.get("month") or "").strip()
        if not month_raw or len(month_raw) != 7 or month_raw[4] != "-":
            raise ValueError("Monthly audit requires 'month' in YYYY-MM format (e.g. '2026-10').")
        yr = int(month_raw[:4])
        mo = int(month_raw[5:7])
        if not (1900 <= yr <= 2200 and 1 <= mo <= 12):
            raise ValueError("Month must be a valid calendar YYYY-MM between 1900 and 2200.")
        start = date(yr, mo, 1)
        end = date(yr, mo, monthrange(yr, mo)[1])
        return start, end, f"{yr:04d}-{mo:02d}", fiscal_month

    if audit_type == "yearly":
        year_val = params.get("year")
        if year_val is None:
            raise ValueError("Yearly audit requires 'year' (e.g. 2026).")
        yr = int(year_val)
        if not (1900 <= yr <= 2200):
            raise ValueError("Year must be between 1900 and 2200.")
        use_calendar = bool(params.get("calendar_year", False)) or fiscal_month == 1
        if use_calendar:
            start = date(yr, 1, 1)
            end = date(yr, 12, 31)
            label = f"CY-{yr}"
        else:
            start = date(yr, fiscal_month, 1)
            end_year = yr + 1
            end_month = fiscal_month - 1
            end = date(end_year, end_month, monthrange(end_year, end_month)[1])
            label = f"FY-{yr}-{end_year}"
        return start, end, label, fiscal_month

    if audit_type in {"emergency", "auto_balance_sheet", "auto_period_close"}:
        end_raw = params.get("end_date")
        end = date.fromisoformat(str(end_raw)) if end_raw else today_dt
        start_raw = params.get("start_date")
        if start_raw:
            start = date.fromisoformat(str(start_raw))
        else:
            start = resolve_earliest_record_date(db, business_id, end)
        if end < start:
            raise ValueError("Audit end_date must be on or after start_date.")
        return start, end, f"{start.isoformat()}..{end.isoformat()}", fiscal_month

    raise ValueError(f"Unsupported audit_type '{audit_type}'.")


def make_validate_request_node(deps: AuditRuntimeDeps) -> Any:
    def validate_request(state: AuditState) -> dict[str, Any]:
        audit_type = str(state.get("audit_type") or "")
        params = dict(state.get("request_params") or {})
        try:
            bid = UUID(str(state["business_id"]))
            start, end, label, fiscal_month = _normalize_period(deps.db, bid, audit_type, params)
            return {
                "validation_valid": True,
                "period": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "label": label,
                    "fiscal_month": fiscal_month,
                },
                "node_trace": ["validate_request"],
            }
        except Exception as exc:
            return {
                "validation_valid": False,
                "errors": [str(exc)],
                "node_trace": ["validate_request"],
            }

    return validate_request


def handle_invalid_request(state: AuditState) -> dict[str, Any]:
    errs = list(state.get("errors") or ["Invalid audit request parameters."])
    msg = "; ".join(errs)
    return {
        "findings": [],
        "severity_summary": {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "total": 0,
            "health_status": "invalid_request",
        },
        "raw_report": {"status": "failed", "errors": errs},
        "polished_report": {
            "executive_summary": f"Audit request could not start: {msg}",
            "severity_breakdown": "Critical: 0 | High: 0 | Medium: 0 | Low: 0 | Total: 0",
            "findings_narrative": [],
            "next_steps": ["Correct the requested audit period or parameters and try again."],
            "markdown": f"# Audit Request Error\n\n{msg}",
            "provider": "validation_gate",
            "model_name": "none",
            "fallback_used": True,
            "fallback_reason": "invalid_request",
            "numeric_guard_passed": True,
            "chunks_processed": 0,
        },
        "node_trace": ["handle_invalid_request"],
    }


def make_load_data_node(deps: AuditRuntimeDeps) -> Any:
    def load_data(state: AuditState) -> dict[str, Any]:
        run_id = str(state["run_id"])
        bid = UUID(str(state["business_id"]))
        period = state["period"]
        start = date.fromisoformat(str(period["start"]))
        end = date.fromisoformat(str(period["end"]))
        dataset = load_audit_dataset(
            deps.db,
            run_id=run_id,
            business_id=bid,
            period_start=start,
            period_end=end,
            scope_filters=state.get("scope_filters"),
        )
        DATASET_REGISTRY.put(run_id, dataset)

        has_any_activity = bool(
            dataset.journals
            or dataset.bank_rows
            or dataset.trade_documents
            or dataset.payments
            or dataset.supplier_payments
            or any(v != 0 for v in dataset.closing_balances.values())
            or dataset.saved_balance_sheets
        )
        summary = {
            "business_name": dataset.business_name,
            "currency": dataset.currency,
            "basis": dataset.basis,
            "accounts_count": len(dataset.accounts),
            "journals_count": len(dataset.journals),
            "posted_journals_count": sum(1 for j in dataset.journals if j["status"] == "posted"),
            "bank_rows_count": len(dataset.bank_rows),
            "bank_rules_count": len(dataset.bank_rules),
            "statements_count": len(dataset.statements),
            "trade_documents_count": len(dataset.trade_documents),
            "payments_count": len(dataset.payments),
            "saved_balance_sheets_count": len(dataset.saved_balance_sheets),
            "periods_count": len(dataset.periods),
        }
        return {
            "has_data": has_any_activity,
            "loaded_data_summary": summary,
            "node_trace": ["load_data"],
        }

    return load_data


def empty_report(state: AuditState) -> dict[str, Any]:
    period = state.get("period") or {}
    summary = state.get("loaded_data_summary") or {}
    start = period.get("start", "")
    end = period.get("end", "")
    sev = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "total": 0,
        "health_status": "nothing_to_audit",
    }
    exec_text = (
        f"Nothing to audit for {start} through {end}: 0 transactions, journals, bank rows, "
        "or non-zero balances were recorded in this period."
    )
    return {
        "findings": [],
        "retrieved_context": [],
        "severity_summary": sev,
        "critical_flagged": False,
        "raw_report": {
            "status": "completed_empty",
            "period": period,
            "loaded_data_summary": summary,
            "severity_summary": sev,
            "findings": [],
        },
        "polished_report": {
            "executive_summary": exec_text,
            "severity_breakdown": "Critical: 0 | High: 0 | Medium: 0 | Low: 0 | Total: 0",
            "findings_narrative": [],
            "next_steps": [
                "Import bank statements or post journal entries for this period before auditing."
            ],
            "markdown": f"# Audit Summary ({start} to {end})\n\n{exec_text}",
            "provider": "deterministic_empty_shortcircuit",
            "model_name": "none",
            "fallback_used": True,
            "fallback_reason": "empty_period",
            "numeric_guard_passed": True,
            "chunks_processed": 0,
        },
        "node_trace": ["empty_report"],
    }


def retrieve_context(state: AuditState) -> dict[str, Any]:
    run_id = str(state["run_id"])
    query = (
        "balance sheet accounting equation continuity subledger control tie-out "
        "bank rules statement reconciliation trade documents evidence supplier payments "
        "period lock anomalies duplicates outliers"
    )
    try:
        res: dict[str, Any] = retrieve_audit_context_tool.invoke(
            {"run_id": run_id, "query": query, "top_k": 10}
        )
        return {
            "retrieved_context": list(res.get("chunks") or []),
            "node_trace": ["retrieve_context"],
        }
    except Exception as exc:
        return {
            "retrieved_context": [],
            "errors": [f"retrieve_context_error:{exc}"],
            "node_trace": ["retrieve_context"],
        }


def run_rules(state: AuditState) -> dict[str, Any]:
    run_id = str(state["run_id"])
    collected_findings: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    errors: list[str] = []

    for rule_tool in (
        evaluate_banking_rules_tool,
        evaluate_accounting_rules_tool,
        evaluate_payment_and_close_rules_tool,
    ):
        try:
            res: dict[str, Any] = rule_tool.invoke({"run_id": run_id})
            tool_findings = list(res.get("findings") or [])
            collected_findings.extend(tool_findings)
            summaries.append(
                {
                    "tool": res.get("tool", rule_tool.name),
                    "findings_count": len(tool_findings),
                }
            )
        except Exception as exc:
            errors.append(f"{rule_tool.name}_error:{exc}")

    return {
        "rule_results": summaries,
        "candidate_findings": collected_findings,
        "errors": errors,
        "node_trace": ["run_rules"],
    }


def reconcile(state: AuditState) -> dict[str, Any]:
    run_id = str(state["run_id"])
    try:
        res: dict[str, Any] = reconcile_balances_and_continuity_tool.invoke({"run_id": run_id})
        tool_findings = list(res.get("findings") or [])
        return {
            "rule_results": [
                {
                    "tool": "reconcile_balances_and_continuity",
                    "findings_count": len(tool_findings),
                    "assets": res.get("assets"),
                    "liabilities": res.get("liabilities"),
                    "equity": res.get("equity"),
                    "retained_earnings": res.get("retained_earnings"),
                }
            ],
            "candidate_findings": tool_findings,
            "node_trace": ["reconcile"],
        }
    except Exception as exc:
        return {
            "errors": [f"reconcile_error:{exc}"],
            "node_trace": ["reconcile"],
        }


def detect_anomalies(state: AuditState) -> dict[str, Any]:
    run_id = str(state["run_id"])
    try:
        res: dict[str, Any] = detect_ledger_and_bank_anomalies_tool.invoke({"run_id": run_id})
        tool_findings = list(res.get("findings") or [])
        return {
            "rule_results": [
                {
                    "tool": "detect_ledger_and_bank_anomalies",
                    "findings_count": len(tool_findings),
                }
            ],
            "candidate_findings": tool_findings,
            "node_trace": ["detect_anomalies"],
        }
    except Exception as exc:
        return {
            "errors": [f"detect_anomalies_error:{exc}"],
            "node_trace": ["detect_anomalies"],
        }


def aggregate_findings(state: AuditState) -> dict[str, Any]:
    raw_candidates = list(state.get("candidate_findings") or [])
    context_chunks = list(state.get("retrieved_context") or [])

    # Deduplicate deterministically by finding id (derived from rule_violated + sorted records)
    deduped_map: dict[str, dict[str, Any]] = {}
    for item in raw_candidates:
        fid = str(item["id"])
        if fid not in deduped_map:
            enriched = dict(item)
            evidence_list = list(enriched.get("evidence") or [])
            # Attach matching RAG policy citation to give contextual grounding
            rule_prefix = str(enriched.get("rule_violated", "")).split(":")[0]
            cat = str(enriched.get("category", ""))
            for chunk in context_chunks:
                if rule_prefix and rule_prefix in str(chunk.get("chunk_id", "")):
                    citation = f"[Policy Context · {chunk['title']}] {chunk['content']}"
                    if citation not in evidence_list:
                        evidence_list.append(citation)
                    break
                if cat and cat in (chunk.get("tags") or []):
                    citation = f"[Policy Context · {chunk['title']}] {chunk['content']}"
                    if citation not in evidence_list:
                        evidence_list.append(citation)
                    break
            enriched["evidence"] = evidence_list
            deduped_map[fid] = enriched

    ordered = sorted(
        deduped_map.values(),
        key=lambda f: (
            _SEVERITY_RANK.get(str(f["severity"]), 9),
            str(f["rule_violated"]),
            str(f["id"]),
        ),
    )

    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for f in ordered:
        sev = str(f["severity"])
        if sev in counts:
            counts[sev] += 1

    total = len(ordered)
    health = (
        "critical_issues"
        if counts["Critical"] > 0
        else "attention_needed"
        if counts["High"] + counts["Medium"] > 0
        else "minor_observations"
        if counts["Low"] > 0
        else "clean_verified"
    )
    severity_summary = {
        "critical": counts["Critical"],
        "high": counts["High"],
        "medium": counts["Medium"],
        "low": counts["Low"],
        "total": total,
        "health_status": health,
    }
    raw_report = {
        "status": "completed",
        "audit_type": state.get("audit_type"),
        "trigger_source": state.get("trigger_source"),
        "period": state.get("period"),
        "loaded_data_summary": state.get("loaded_data_summary"),
        "severity_summary": severity_summary,
        "rule_results": list(state.get("rule_results") or []),
        "findings": ordered,
        "critical_escalation": None,
    }
    return {
        "findings": ordered,
        "severity_summary": severity_summary,
        "critical_flagged": counts["Critical"] > 0,
        "raw_report": raw_report,
        "node_trace": ["aggregate_findings"],
    }


def flag_critical_findings(state: AuditState) -> dict[str, Any]:
    """Dedicated conditional branch flagging Critical integrity issues before polishing."""
    raw = dict(state.get("raw_report") or {})
    crit_items = [
        {
            "id": f["id"],
            "rule_violated": f["rule_violated"],
            "affected_records": f["affected_records"],
        }
        for f in (state.get("findings") or [])
        if f.get("severity") == "Critical"
    ]
    raw["critical_escalation"] = {
        "close_freeze_recommended": True,
        "critical_count": len(crit_items),
        "critical_items": crit_items,
        "banner": (
            "CRITICAL ACCOUNTING INTEGRITY ALERT: Do not lock periods or distribute external "
            "financial statements until all Critical findings are remediated."
        ),
    }
    return {
        "critical_flagged": True,
        "raw_report": raw,
        "node_trace": ["flag_critical_findings"],
    }


def make_polish_report_node(deps: AuditRuntimeDeps) -> Any:
    def polish_report(state: AuditState) -> dict[str, Any]:
        polished, usage_meta, needs_fallback = polish_with_groq(
            settings=deps.settings,
            adapter=deps.adapter,
            audit_type=str(state.get("audit_type", "emergency")),
            period=dict(state.get("period") or {}),
            loaded_data_summary=dict(state.get("loaded_data_summary") or {}),
            severity_summary=dict(state.get("severity_summary") or {}),
            findings=list(state.get("findings") or []),
            critical_flagged=bool(state.get("critical_flagged", False)),
            rule_results=list(state.get("rule_results") or []),
        )
        meta = dict(state.get("metadata") or {})
        meta.update(usage_meta)
        if needs_fallback:
            meta["fallback_reason"] = polished.get("fallback_reason", "templated_fallback")
            return {
                "polish_needs_fallback": True,
                "metadata": meta,
                "node_trace": ["polish_report"],
            }
        return {
            "polish_needs_fallback": False,
            "polished_report": polished,
            "metadata": meta,
            "node_trace": ["polish_report"],
        }

    return polish_report


def fallback_report(state: AuditState) -> dict[str, Any]:
    """Deterministic fallback node reached when Groq is unconfigured, rate-limited, or rejected."""
    meta = dict(state.get("metadata") or {})
    reason = str(meta.get("fallback_reason") or "templated_fallback")
    templated = build_templated_report(
        audit_type=str(state.get("audit_type", "emergency")),
        period=dict(state.get("period") or {}),
        loaded_data_summary=dict(state.get("loaded_data_summary") or {}),
        severity_summary=dict(state.get("severity_summary") or {}),
        findings=list(state.get("findings") or []),
        critical_flagged=bool(state.get("critical_flagged", False)),
        fallback_reason=reason,
    )
    if reason.startswith("numeric_guard_rejected"):
        templated["numeric_guard_passed"] = False
    return {
        "polished_report": templated,
        "node_trace": ["fallback_report"],
    }


def make_persist_and_return_node(deps: AuditRuntimeDeps) -> Any:
    def persist_and_return(state: AuditState) -> dict[str, Any]:
        run_id = str(state["run_id"])
        DATASET_REGISTRY.pop(run_id)

        valid = bool(state.get("validation_valid", False))
        has_data = bool(state.get("has_data", False))
        status = "failed" if not valid else "completed_empty" if not has_data else "completed"
        trace = [*list(state.get("node_trace") or []), "persist_and_return"]
        meta = dict(state.get("metadata") or {})
        meta["node_trace"] = trace
        meta["completed_at"] = datetime.now(UTC).isoformat()

        if deps.persist and valid:
            period = state.get("period") or {}
            start_dt = date.fromisoformat(str(period["start"]))
            end_dt = date.fromisoformat(str(period["end"]))
            bid = UUID(str(state["business_id"]))
            actor_id = UUID(str(state["actor_id"]))
            run_uuid = UUID(run_id)
            job_id_raw = meta.get("job_id")
            job_uuid = UUID(str(job_id_raw)) if job_id_raw else None

            try:
                with deps.db.begin_nested():
                    existing = deps.db.get(FinancialAuditRun, run_uuid)
                    if existing is not None:
                        existing.status = status
                        existing.loaded_data_summary = dict(state.get("loaded_data_summary") or {})
                        existing.severity_summary = dict(state.get("severity_summary") or {})
                        existing.findings = list(state.get("findings") or [])
                        existing.raw_report = dict(state.get("raw_report") or {})
                        existing.polished_report = dict(state.get("polished_report") or {})
                        existing.retrieved_context = list(state.get("retrieved_context") or [])
                        existing.metadata_json = meta
                    else:
                        deps.db.add(
                            FinancialAuditRun(
                                id=run_uuid,
                                business_id=bid,
                                audit_type=str(state.get("audit_type", "emergency")),
                                trigger_source=str(state.get("trigger_source", "manual")),
                                period_start=start_dt,
                                period_end=end_dt,
                                scope_filters=dict(state.get("scope_filters") or {}),
                                status=status,
                                job_id=job_uuid,
                                thread_id=str(state.get("thread_id", run_id)),
                                loaded_data_summary=dict(state.get("loaded_data_summary") or {}),
                                severity_summary=dict(state.get("severity_summary") or {}),
                                findings=list(state.get("findings") or []),
                                raw_report=dict(state.get("raw_report") or {}),
                                polished_report=dict(state.get("polished_report") or {}),
                                retrieved_context=list(state.get("retrieved_context") or []),
                                metadata_json=meta,
                                created_by=actor_id,
                            )
                        )
                    record_audit_event(
                        deps.db,
                        actor_id,
                        "financial_audit.completed",
                        bid,
                        run_uuid,
                        {"status": status},
                    )
            except Exception as exc:
                meta["persistence_warning"] = type(exc).__name__

        return {
            "metadata": meta,
            "node_trace": ["persist_and_return"],
        }

    return persist_and_return
