"""Groq report polishing layer with batch chunking, numeric hallucination guard, and fallback."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ledgerpilot.ai.adapter import GroqModelAdapter, ModelGenerationResult
from ledgerpilot.config import Settings

_NUMBER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])-?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_-])")
CHUNK_SIZE = 12


def _normalize_numeric_str(token: str) -> Decimal | None:
    cleaned = token.replace(",", "").strip()
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        val = Decimal(cleaned)
        return val if val.is_finite() else None
    except InvalidOperation:
        return None


def collect_allowed_numbers(
    *,
    findings: list[dict[str, Any]],
    severity_summary: dict[str, Any],
    loaded_data_summary: dict[str, Any],
    period: dict[str, Any],
    rule_results: list[dict[str, Any]] | None = None,
) -> set[Decimal]:
    """Collect every legitimate numeric value present in structured audit inputs."""
    allowed: set[Decimal] = {Decimal(i) for i in range(0, max(25, len(findings) + 10))}

    def _harvest(obj: Any) -> None:
        if isinstance(obj, bool) or obj is None:
            return
        if isinstance(obj, int | float | Decimal):
            parsed = _normalize_numeric_str(str(obj))
            if parsed is not None:
                allowed.add(parsed)
                allowed.add(abs(parsed))
            return
        if isinstance(obj, str):
            for match in _NUMBER_TOKEN_RE.findall(obj):
                parsed = _normalize_numeric_str(match)
                if parsed is not None:
                    allowed.add(parsed)
                    allowed.add(abs(parsed))
            # Also harvest YYYY / MM / DD parts from ISO dates (e.g. "2026-10-01")
            for date_part in re.findall(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", obj):
                for seg in date_part:
                    if seg:
                        allowed.add(Decimal(int(seg)))
            return
        if isinstance(obj, dict):
            for v in obj.values():
                _harvest(v)
        elif isinstance(obj, list | tuple | set):
            for item in obj:
                _harvest(item)

    _harvest(findings)
    _harvest(severity_summary)
    _harvest(loaded_data_summary)
    _harvest(period)
    if rule_results:
        _harvest(rule_results)
    return allowed


def verify_no_invented_numbers(text_to_check: str, allowed_numbers: set[Decimal]) -> list[str]:
    """Return any numeric tokens in text_to_check whose value is absent from allowed_numbers."""
    invented: list[str] = []
    # Strip UUID-like hex strings and rule codes (e.g. RULE-BS-001, FIND-0001) before scanning
    scrubbed = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        " ",
        text_to_check,
    )
    scrubbed = re.sub(r"\b[A-Z]+-[A-Z0-9-]+-\d+\b", " ", scrubbed)
    scrubbed = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", scrubbed)
    scrubbed = re.sub(r"\b\d{4}-\d{2}\b", " ", scrubbed)

    for token in _NUMBER_TOKEN_RE.findall(scrubbed):
        parsed = _normalize_numeric_str(token)
        if parsed is None:
            continue
        if parsed in allowed_numbers or abs(parsed) in allowed_numbers:
            continue
        # Also allow quantized 2-decimal representation of an allowed number
        quant2 = parsed.quantize(Decimal("0.01"))
        if any(
            n == quant2 or abs(n).quantize(Decimal("0.01")) == abs(quant2) for n in allowed_numbers
        ):
            continue
        invented.append(token)
    return invented


def build_templated_report(
    *,
    audit_type: str,
    period: dict[str, Any],
    loaded_data_summary: dict[str, Any],
    severity_summary: dict[str, Any],
    findings: list[dict[str, Any]],
    critical_flagged: bool,
    fallback_reason: str = "templated_deterministic",
) -> dict[str, Any]:
    """Deterministic, plain-language report builder guaranteed to preserve exact figures."""
    start = period.get("start", "")
    end = period.get("end", "")
    currency = loaded_data_summary.get("currency", "USD")
    total = int(severity_summary.get("total", len(findings)))
    crit = int(severity_summary.get("critical", 0))
    high = int(severity_summary.get("high", 0))
    med = int(severity_summary.get("medium", 0))
    low = int(severity_summary.get("low", 0))

    mode_label = audit_type.replace("_", " ").title()
    if total == 0:
        exec_summary = (
            f"{mode_label} audit for {start} through {end} ({currency}) completed with "
            f"0 findings across {loaded_data_summary.get('journals_count', 0)} journals, "
            f"{loaded_data_summary.get('bank_rows_count', 0)} bank transactions, and "
            f"{loaded_data_summary.get('trade_documents_count', 0)} trade documents. "
            "All balance sheet equations, period continuities, and banking rules tied out."
        )
    else:
        prefix = "URGENT REVIEW REQUIRED: " if critical_flagged else ""
        exec_summary = (
            f"{prefix}{mode_label} audit for {start} through {end} ({currency}) identified "
            f"{total} finding(s): {crit} Critical, {high} High, {med} Medium, and {low} Low."
        )

    sev_line = f"Critical: {crit} | High: {high} | Medium: {med} | Low: {low} | Total: {total}"

    finding_items: list[dict[str, str]] = []
    md_lines = [
        f"# {mode_label} Financial Audit Report ({start} to {end})",
        "",
        "## Executive Summary",
        exec_summary,
        "",
        "## Severity Breakdown",
        f"- **Critical:** {crit}",
        f"- **High:** {high}",
        f"- **Medium:** {med}",
        f"- **Low:** {low}",
        f"- **Total Findings:** {total}",
        "",
    ]

    if critical_flagged:
        md_lines.extend(
            [
                "## Critical Alert",
                "Hold period close and external reporting until all Critical findings below "
                "are resolved and verified.",
                "",
            ]
        )

    md_lines.append("## Findings")
    if not findings:
        md_lines.append("No rule violations, balance discrepancies, or anomalies were detected.")
    else:
        for idx, f in enumerate(findings, start=1):
            exp_act = ", ".join(f"{k}={v}" for k, v in (f.get("expected_vs_actual") or {}).items())
            where_str = ", ".join(f.get("affected_records") or []) or "Workspace scope"
            what_str = f"[{f['severity']}] {f['rule_violated']} ({f['category']})"
            why_str = f"Observed mismatch ({exp_act}). " + "; ".join((f.get("evidence") or [])[:2])
            fix_str = str(f.get("recommendation", ""))
            finding_items.append(
                {
                    "id": str(f["id"]),
                    "severity": str(f["severity"]),
                    "what": what_str,
                    "where": where_str,
                    "why_it_matters": why_str,
                    "recommended_fix": fix_str,
                }
            )
            md_lines.extend(
                [
                    f"### {idx}. {what_str}",
                    f"- **Where:** {where_str}",
                    f"- **Figures:** {exp_act or 'See evidence'}",
                    f"- **Why it matters:** {why_str}",
                    f"- **Recommended fix:** {fix_str}",
                    "",
                ]
            )

    next_steps: list[str] = []
    if crit > 0:
        next_steps.append(
            f"Resolve the {crit} Critical finding(s) before locking periods or sharing reports."
        )
    if high > 0:
        next_steps.append(
            f"Review and correct the {high} High-severity rule or reconciliation issue(s)."
        )
    if med + low > 0:
        next_steps.append(
            f"Triage the remaining {med + low} Medium/Low item(s) during routine review."
        )
    if not next_steps:
        next_steps.append("Books are consistent for this period; proceed with normal operations.")

    md_lines.append("## Next Steps")
    for step in next_steps:
        md_lines.append(f"- {step}")

    return {
        "executive_summary": exec_summary,
        "severity_breakdown": sev_line,
        "findings_narrative": finding_items,
        "next_steps": next_steps,
        "markdown": "\n".join(md_lines),
        "provider": "deterministic_template",
        "model_name": "deterministic-audit-formatter-v1",
        "fallback_used": True,
        "fallback_reason": fallback_reason,
        "numeric_guard_passed": True,
        "chunks_processed": max(1, (len(findings) + CHUNK_SIZE - 1) // CHUNK_SIZE),
    }


def polish_with_groq(
    *,
    settings: Settings,
    adapter: GroqModelAdapter | None,
    audit_type: str,
    period: dict[str, Any],
    loaded_data_summary: dict[str, Any],
    severity_summary: dict[str, Any],
    findings: list[dict[str, Any]],
    critical_flagged: bool,
    rule_results: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Polish structured findings via Groq with hierarchical chunking and strict number checking.

    Returns (polished_payload, usage_meta, needs_fallback_node).
    """
    baseline = build_templated_report(
        audit_type=audit_type,
        period=period,
        loaded_data_summary=loaded_data_summary,
        severity_summary=severity_summary,
        findings=findings,
        critical_flagged=critical_flagged,
        fallback_reason="groq_not_configured",
    )
    model_adapter = adapter or GroqModelAdapter(settings)
    if not model_adapter.is_configured():
        return (
            baseline,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": "0.000000",
                "latency_ms": 0,
            },
            True,
        )

    allowed_numbers = collect_allowed_numbers(
        findings=findings,
        severity_summary=severity_summary,
        loaded_data_summary=loaded_data_summary,
        period=period,
        rule_results=rule_results,
    )

    system_prompt = (
        "You are a senior financial audit communicator. Convert the supplied verified "
        "accounting audit findings into clear, plain English for business owners and "
        "accountants. STRICT RULES: "
        "1) NEVER invent, round, alter, add, or omit any numbers, amounts, counts, or dates. "
        "2) Use ONLY exact figures present in the input JSON. "
        "3) Return valid JSON with keys: executive_summary (string), severity_breakdown (string), "
        "findings_narrative (array of objects with id, severity, what, where, why_it_matters, "
        "recommended_fix), next_steps (array of strings), and markdown (string)."
    )

    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_latency_ms = 0
    total_cost = Decimal("0.000000")
    chunk_summaries: list[str] = []

    chunks = [findings[i : i + CHUNK_SIZE] for i in range(0, len(findings), CHUNK_SIZE)] or [[]]
    if len(chunks) > 1:
        for idx, chunk in enumerate(chunks, start=1):
            chunk_fallback = {
                "chunk_summary": (f"Batch {idx} covers {len(chunk)} verified finding(s).")
            }
            chunk_res: ModelGenerationResult = model_adapter.generate_structured(
                feature_id="AUDIT-CHUNK",
                system_prompt=(
                    "Summarize this batch of verified audit findings concisely in JSON "
                    '{"chunk_summary": "..."} using ONLY exact numbers from the input.'
                ),
                user_prompt=json.dumps({"batch_index": idx, "findings": chunk}, sort_keys=True),
                fallback_payload=chunk_fallback,
            )
            total_prompt_tokens += chunk_res.prompt_tokens
            total_completion_tokens += chunk_res.completion_tokens
            total_latency_ms += chunk_res.latency_ms
            total_cost += chunk_res.estimated_cost_usd
            if chunk_res.fallback_used:
                baseline["fallback_reason"] = chunk_res.fallback_reason or "chunk_model_fallback"
                return (
                    baseline,
                    {
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                        "estimated_cost_usd": f"{total_cost:.6f}",
                        "latency_ms": total_latency_ms,
                    },
                    True,
                )
            chunk_summaries.append(str(chunk_res.structured.get("chunk_summary", "")))

    synthesis_input = {
        "audit_type": audit_type,
        "period": period,
        "loaded_data_summary": loaded_data_summary,
        "severity_summary": severity_summary,
        "critical_flagged": critical_flagged,
        "chunk_summaries": chunk_summaries,
        "findings": findings[:CHUNK_SIZE] if len(chunks) > 1 else findings,
        "total_findings_count": len(findings),
    }

    gen_res = model_adapter.generate_structured(
        feature_id="AUDIT-POLISH",
        system_prompt=system_prompt,
        user_prompt=json.dumps(synthesis_input, sort_keys=True),
        fallback_payload={
            "executive_summary": baseline["executive_summary"],
            "severity_breakdown": baseline["severity_breakdown"],
            "findings_narrative": baseline["findings_narrative"],
            "next_steps": baseline["next_steps"],
            "markdown": baseline["markdown"],
        },
    )
    total_prompt_tokens += gen_res.prompt_tokens
    total_completion_tokens += gen_res.completion_tokens
    total_latency_ms += gen_res.latency_ms
    total_cost += gen_res.estimated_cost_usd

    usage_meta = {
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "estimated_cost_usd": f"{total_cost:.6f}",
        "latency_ms": total_latency_ms,
    }

    if gen_res.fallback_used:
        baseline["fallback_reason"] = gen_res.fallback_reason or "groq_fallback"
        return baseline, usage_meta, True

    candidate = gen_res.structured
    combined_text = json.dumps(
        {
            "executive_summary": candidate.get("executive_summary", ""),
            "severity_breakdown": candidate.get("severity_breakdown", ""),
            "findings_narrative": candidate.get("findings_narrative", []),
            "next_steps": candidate.get("next_steps", []),
            "markdown": candidate.get("markdown", ""),
        }
    )
    invented = verify_no_invented_numbers(combined_text, allowed_numbers)
    if invented:
        baseline["fallback_reason"] = f"numeric_guard_rejected:{','.join(invented[:5])}"
        baseline["numeric_guard_passed"] = False
        return baseline, usage_meta, True

    # If chunked, ensure findings_narrative still lists all structured findings accurately
    narratives = candidate.get("findings_narrative")
    if not isinstance(narratives, list) or len(narratives) != len(findings):
        narratives = baseline["findings_narrative"]

    polished = {
        "executive_summary": str(
            candidate.get("executive_summary") or baseline["executive_summary"]
        ),
        "severity_breakdown": str(
            candidate.get("severity_breakdown") or baseline["severity_breakdown"]
        ),
        "findings_narrative": narratives,
        "next_steps": (
            [str(s) for s in candidate["next_steps"]]
            if isinstance(candidate.get("next_steps"), list) and candidate["next_steps"]
            else baseline["next_steps"]
        ),
        "markdown": str(candidate.get("markdown") or baseline["markdown"]),
        "provider": gen_res.provider,
        "model_name": gen_res.model_name,
        "fallback_used": False,
        "fallback_reason": "",
        "numeric_guard_passed": True,
        "chunks_processed": len(chunks),
    }
    return polished, usage_meta, False
