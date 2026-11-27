"""Conditional routing functions for the LangGraph financial audit workflow."""

from __future__ import annotations

from typing import Literal

from ledgerpilot.audit.state import AuditState

ParallelTarget = Literal[
    "empty_report",
    "retrieve_context",
    "run_rules",
    "reconcile",
    "detect_anomalies",
]


def route_after_validate(
    state: AuditState,
) -> Literal["load_data", "handle_invalid_request"]:
    """Route valid requests to load_data; route invalid inputs to handle_invalid_request."""
    if state.get("validation_valid"):
        return "load_data"
    return "handle_invalid_request"


def route_after_load(state: AuditState) -> list[ParallelTarget]:
    """Short-circuit empty periods; otherwise fan out all four audit branches in parallel."""
    if not state.get("has_data"):
        return ["empty_report"]
    return [
        "retrieve_context",
        "run_rules",
        "reconcile",
        "detect_anomalies",
    ]


def route_after_aggregate(
    state: AuditState,
) -> Literal["flag_critical_findings", "polish_report"]:
    """Route runs with Critical findings through the dedicated escalation node before polishing."""
    sev = state.get("severity_summary") or {}
    if int(sev.get("critical", 0)) > 0:
        return "flag_critical_findings"
    return "polish_report"


def route_after_polish(
    state: AuditState,
) -> Literal["fallback_report", "persist_and_return"]:
    """Route Groq failures, rate limits, or numeric-guard rejections to deterministic fallback."""
    if state.get("polish_needs_fallback"):
        return "fallback_report"
    return "persist_and_return"
