"""Strictly read-only, tenant-scoped accounting tools with schema validation and shield."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.ai.adapter import sanitize_untrusted_text
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema

TOOL_VERSIONS: dict[str, str] = {
    "get_account_balances": "1.0.0",
    "get_open_trade_documents": "1.0.0",
    "get_unmatched_bank_rows": "1.0.0",
    "lookup_approved_memories": "1.0.0",
    "get_close_readiness_snapshot": "1.0.0",
}


class ToolInvocationAudit(Schema):
    tool_name: str
    tool_version: str
    parameters_hash: str
    row_count: int
    result_bytes: int
    untrusted_content_sanitized: bool
    duration_ms: int


class AccountBalancesToolInput(Schema):
    business_id: UUID
    kind_filter: Literal["all", "asset", "liability", "equity", "income", "expense"] = "all"
    limit: int = Field(default=25, ge=1, le=50)


class AccountBalanceRow(Schema):
    account_id: str
    code: str
    name: str
    kind: str
    control: str | None = None
    total_debit: str
    total_credit: str
    net_balance: str


class AccountBalancesToolOutput(Schema):
    business_id: str
    currency: str
    basis: str
    business_version: int
    items: list[AccountBalanceRow]
    audit: ToolInvocationAudit


class OpenDocumentsToolInput(Schema):
    business_id: UUID
    document_kind: Literal["all", "invoice", "bill"] = "all"
    limit: int = Field(default=25, ge=1, le=50)


class OpenDocumentRow(Schema):
    document_id: str
    kind: str
    number: str
    issue_date: str
    due_date: str
    total: str
    allocated: str
    outstanding: str
    disputed: bool
    has_evidence: bool


class OpenDocumentsToolOutput(Schema):
    business_id: str
    items: list[OpenDocumentRow]
    audit: ToolInvocationAudit


class UnmatchedBankRowsToolInput(Schema):
    business_id: UUID
    limit: int = Field(default=25, ge=1, le=50)


class UnmatchedBankRowItem(Schema):
    row_id: str
    posted_date: str
    amount: str
    description: str
    reference: str
    state: str


class UnmatchedBankRowsToolOutput(Schema):
    business_id: str
    items: list[UnmatchedBankRowItem]
    audit: ToolInvocationAudit


class ApprovedMemoriesToolInput(Schema):
    business_id: UUID
    accounting_framework: Literal["US_GAAP", "IFRS", "TAX_BASIS", "ALL"] = "ALL"
    search_query: str = Field(default="", max_length=120)
    as_of_date: date | None = None
    limit: int = Field(default=20, ge=1, le=50)


class ApprovedMemoryItem(Schema):
    memory_id: str
    memory_key: str
    title: str
    accounting_framework: str
    category: str
    binding_kind: str
    rule_condition: str
    account_code: str
    account_name: str
    materiality_threshold: str
    effective_from: str
    effective_to: str | None = None
    version: int


class ApprovedMemoriesToolOutput(Schema):
    business_id: str
    items: list[ApprovedMemoryItem]
    audit: ToolInvocationAudit


class CloseReadinessToolInput(Schema):
    business_id: UUID


class CloseReadinessToolOutput(Schema):
    business_id: str
    pending_bank_rows: int
    bills_missing_evidence: int
    open_close_tasks: int
    locked_periods: int
    open_periods: int
    audit: ToolInvocationAudit


def _verify_rls_scope(db: Session, business_id: UUID) -> None:
    """Ensure the session has bound app.scope() and the caller holds active membership."""
    row = (
        db.execute(
            text(
                """
                SELECT (app.scope() = :bid) AS scoped,
                       app.has_access(:bid) AS allowed
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .one()
    )
    if not row["scoped"] or not row["allowed"]:
        raise DomainError("ERR_FORBIDDEN", "Tool call violates tenant authorization boundary.", 403)


def _hash_params(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def tool_get_account_balances(
    db: Session,
    params: AccountBalancesToolInput,
) -> AccountBalancesToolOutput:
    """Read-only tool: fetch account balances from posted journal lines in canonical Decimal."""
    started = time.monotonic()
    _verify_rls_scope(db, params.business_id)
    biz = (
        db.execute(
            text("SELECT currency, basis, version FROM app.businesses WHERE id = :bid"),
            {"bid": params.business_id},
        )
        .mappings()
        .first()
    )
    if not biz:
        raise DomainError("ERR_NOT_FOUND", "Workspace not found.", 404)

    rows = (
        db.execute(
            text(
                """
                SELECT a.id, a.code, a.name, a.kind, a.control,
                       COALESCE(SUM(jl.debit), 0) AS total_debit,
                       COALESCE(SUM(jl.credit), 0) AS total_credit
                FROM app.accounts a
                LEFT JOIN app.journals j
                  ON j.business_id = a.business_id AND j.status = 'posted'
                LEFT JOIN app.journal_lines jl
                  ON jl.business_id = a.business_id
                 AND jl.journal_id = j.id
                 AND jl.account_id = a.id
                WHERE a.business_id = :bid
                  AND a.active = true
                  AND (:kfilter = 'all' OR a.kind = :kfilter)
                GROUP BY a.id, a.code, a.name, a.kind, a.control
                ORDER BY a.code, a.id
                LIMIT :lim
                """
            ),
            {
                "bid": params.business_id,
                "kfilter": params.kind_filter,
                "lim": params.limit,
            },
        )
        .mappings()
        .all()
    )

    items: list[AccountBalanceRow] = []
    any_sanitized = False
    for r in rows:
        clean_name, was_san = sanitize_untrusted_text(str(r["name"]), max_length=120)
        any_sanitized = any_sanitized or was_san
        d = Decimal(str(r["total_debit"] or "0")).quantize(Decimal("0.01"))
        c = Decimal(str(r["total_credit"] or "0")).quantize(Decimal("0.01"))
        net = (d - c) if r["kind"] in ("asset", "expense") else (c - d)
        items.append(
            AccountBalanceRow(
                account_id=str(r["id"]),
                code=str(r["code"]),
                name=clean_name,
                kind=str(r["kind"]),
                control=str(r["control"]) if r["control"] else None,
                total_debit=f"{d:.2f}",
                total_credit=f"{c:.2f}",
                net_balance=f"{net:.2f}",
            )
        )

    serialized = json.dumps([i.model_dump() for i in items])
    audit = ToolInvocationAudit(
        tool_name="get_account_balances",
        tool_version=TOOL_VERSIONS["get_account_balances"],
        parameters_hash=_hash_params(params.model_dump()),
        row_count=len(items),
        result_bytes=len(serialized.encode("utf-8")),
        untrusted_content_sanitized=any_sanitized,
        duration_ms=max(1, int((time.monotonic() - started) * 1000)),
    )
    return AccountBalancesToolOutput(
        business_id=str(params.business_id),
        currency=str(biz["currency"]),
        basis=str(biz["basis"]),
        business_version=int(biz["version"]),
        items=items,
        audit=audit,
    )


def tool_get_open_trade_documents(
    db: Session,
    params: OpenDocumentsToolInput,
) -> OpenDocumentsToolOutput:
    """Read-only tool: fetch unpaid or partially paid invoices/bills with evidence status."""
    started = time.monotonic()
    _verify_rls_scope(db, params.business_id)
    rows = (
        db.execute(
            text(
                """
                SELECT d.id, d.kind, d.number,
                       d.accounting_date AS issue_date, d.due_date, d.total, d.disputed,
                       COALESCE(SUM(a.amount), 0) AS allocated,
                       EXISTS (
                         SELECT 1 FROM app.evidence_links el
                         WHERE el.business_id = d.business_id
                           AND el.resource_kind = 'trade_document'
                           AND el.resource_id = d.id
                       ) AS has_evidence
                FROM app.trade_documents d
                LEFT JOIN app.allocations a
                  ON a.business_id = d.business_id AND a.document_id = d.id
                WHERE d.business_id = :bid
                  AND d.kind IN ('invoice', 'bill')
                  AND (:dkind = 'all' OR d.kind = :dkind)
                GROUP BY d.id, d.kind, d.number, d.accounting_date, d.due_date, d.total, d.disputed
                HAVING COALESCE(SUM(a.amount), 0) < d.total
                ORDER BY d.due_date ASC, d.id ASC
                LIMIT :lim
                """
            ),
            {
                "bid": params.business_id,
                "dkind": params.document_kind,
                "lim": params.limit,
            },
        )
        .mappings()
        .all()
    )
    items: list[OpenDocumentRow] = []
    any_sanitized = False
    for r in rows:
        clean_num, was_san = sanitize_untrusted_text(str(r["number"]), max_length=80)
        any_sanitized = any_sanitized or was_san
        tot = Decimal(str(r["total"] or "0")).quantize(Decimal("0.01"))
        alloc = Decimal(str(r["allocated"] or "0")).quantize(Decimal("0.01"))
        out = max(Decimal("0.00"), tot - alloc)
        items.append(
            OpenDocumentRow(
                document_id=str(r["id"]),
                kind=str(r["kind"]),
                number=clean_num,
                issue_date=str(r["issue_date"]),
                due_date=str(r["due_date"]),
                total=f"{tot:.2f}",
                allocated=f"{alloc:.2f}",
                outstanding=f"{out:.2f}",
                disputed=bool(r["disputed"]),
                has_evidence=bool(r["has_evidence"]),
            )
        )
    serialized = json.dumps([i.model_dump() for i in items])
    audit = ToolInvocationAudit(
        tool_name="get_open_trade_documents",
        tool_version=TOOL_VERSIONS["get_open_trade_documents"],
        parameters_hash=_hash_params(params.model_dump()),
        row_count=len(items),
        result_bytes=len(serialized.encode("utf-8")),
        untrusted_content_sanitized=any_sanitized,
        duration_ms=max(1, int((time.monotonic() - started) * 1000)),
    )
    return OpenDocumentsToolOutput(
        business_id=str(params.business_id),
        items=items,
        audit=audit,
    )


def tool_get_unmatched_bank_rows(
    db: Session,
    params: UnmatchedBankRowsToolInput,
) -> UnmatchedBankRowsToolOutput:
    """Read-only tool: fetch bank statement rows awaiting review with string sanitization."""
    started = time.monotonic()
    _verify_rls_scope(db, params.business_id)
    rows = (
        db.execute(
            text(
                """
                SELECT id, accounting_date AS posted_date, amount, description,
                       COALESCE(external_id, '') AS reference, state
                FROM app.bank_rows
                WHERE business_id = :bid AND state = 'for_review'
                ORDER BY accounting_date ASC, id ASC
                LIMIT :lim
                """
            ),
            {"bid": params.business_id, "lim": params.limit},
        )
        .mappings()
        .all()
    )
    items: list[UnmatchedBankRowItem] = []
    any_sanitized = False
    for r in rows:
        desc, san_d = sanitize_untrusted_text(str(r["description"] or ""), max_length=240)
        ref, san_r = sanitize_untrusted_text(str(r["reference"] or ""), max_length=120)
        any_sanitized = any_sanitized or san_d or san_r
        amt = Decimal(str(r["amount"] or "0")).quantize(Decimal("0.01"))
        items.append(
            UnmatchedBankRowItem(
                row_id=str(r["id"]),
                posted_date=str(r["posted_date"]),
                amount=f"{amt:.2f}",
                description=desc,
                reference=ref,
                state=str(r["state"]),
            )
        )
    serialized = json.dumps([i.model_dump() for i in items])
    audit = ToolInvocationAudit(
        tool_name="get_unmatched_bank_rows",
        tool_version=TOOL_VERSIONS["get_unmatched_bank_rows"],
        parameters_hash=_hash_params(params.model_dump()),
        row_count=len(items),
        result_bytes=len(serialized.encode("utf-8")),
        untrusted_content_sanitized=any_sanitized,
        duration_ms=max(1, int((time.monotonic() - started) * 1000)),
    )
    return UnmatchedBankRowsToolOutput(
        business_id=str(params.business_id),
        items=items,
        audit=audit,
    )


def tool_lookup_approved_memories(
    db: Session,
    params: ApprovedMemoriesToolInput,
) -> ApprovedMemoriesToolOutput:
    """Read-only tool: retrieve active approved business accounting memories by framework."""
    started = time.monotonic()
    _verify_rls_scope(db, params.business_id)
    target_date = params.as_of_date or date.today()
    q = f"%{params.search_query.strip().lower()}%" if params.search_query.strip() else "%"
    rows = (
        db.execute(
            text(
                """
                SELECT id, memory_key, title, accounting_framework, category, binding_kind,
                       rule_condition, account_code, account_name, materiality_threshold,
                       effective_from, effective_to, version
                FROM app.ai_accounting_memories
                WHERE business_id = :bid
                  AND status = 'approved'
                  AND effective_from <= :as_of
                  AND (effective_to IS NULL OR effective_to >= :as_of)
                  AND (:fw = 'ALL' OR accounting_framework = :fw)
                  AND (
                    lower(title) LIKE :q
                    OR lower(rule_condition) LIKE :q
                    OR lower(memory_key) LIKE :q
                    OR lower(account_name) LIKE :q
                  )
                ORDER BY (binding_kind = 'binding_policy') DESC, effective_from DESC, version DESC
                LIMIT :lim
                """
            ),
            {
                "bid": params.business_id,
                "as_of": target_date,
                "fw": params.accounting_framework,
                "q": q,
                "lim": params.limit,
            },
        )
        .mappings()
        .all()
    )
    items: list[ApprovedMemoryItem] = []
    any_sanitized = False
    for r in rows:
        cond, san_c = sanitize_untrusted_text(str(r["rule_condition"]), max_length=300)
        title, san_t = sanitize_untrusted_text(str(r["title"]), max_length=140)
        any_sanitized = any_sanitized or san_c or san_t
        mat = Decimal(str(r["materiality_threshold"] or "0")).quantize(Decimal("0.01"))
        items.append(
            ApprovedMemoryItem(
                memory_id=str(r["id"]),
                memory_key=str(r["memory_key"]),
                title=title,
                accounting_framework=str(r["accounting_framework"]),
                category=str(r["category"]),
                binding_kind=str(r["binding_kind"]),
                rule_condition=cond,
                account_code=str(r["account_code"]),
                account_name=str(r["account_name"]),
                materiality_threshold=f"{mat:.2f}",
                effective_from=str(r["effective_from"]),
                effective_to=str(r["effective_to"]) if r["effective_to"] else None,
                version=int(r["version"]),
            )
        )
    serialized = json.dumps([i.model_dump() for i in items])
    audit = ToolInvocationAudit(
        tool_name="lookup_approved_memories",
        tool_version=TOOL_VERSIONS["lookup_approved_memories"],
        parameters_hash=_hash_params(params.model_dump()),
        row_count=len(items),
        result_bytes=len(serialized.encode("utf-8")),
        untrusted_content_sanitized=any_sanitized,
        duration_ms=max(1, int((time.monotonic() - started) * 1000)),
    )
    return ApprovedMemoriesToolOutput(
        business_id=str(params.business_id),
        items=items,
        audit=audit,
    )


def tool_get_close_readiness_snapshot(
    db: Session,
    params: CloseReadinessToolInput,
) -> CloseReadinessToolOutput:
    """Read-only tool: compute period close blockers across bank rows, bills, and tasks."""
    started = time.monotonic()
    _verify_rls_scope(db, params.business_id)
    counts = (
        db.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM app.bank_rows
                   WHERE business_id = :bid AND state = 'for_review') AS pending_bank_rows,
                  (SELECT count(*) FROM app.trade_documents d
                   WHERE d.business_id = :bid AND d.kind = 'bill'
                     AND NOT EXISTS (
                       SELECT 1 FROM app.evidence_links el
                       WHERE el.business_id = :bid
                         AND el.resource_kind = 'trade_document'
                         AND el.resource_id = d.id
                     )) AS bills_missing_evidence,
                  (SELECT count(*) FROM app.close_tasks
                   WHERE business_id = :bid AND state = 'prepared') AS open_close_tasks,
                  (SELECT count(*) FROM app.periods
                   WHERE business_id = :bid AND locked = true) AS locked_periods,
                  (SELECT count(*) FROM app.periods
                   WHERE business_id = :bid AND locked = false) AS open_periods
                """
            ),
            {"bid": params.business_id},
        )
        .mappings()
        .one()
    )
    payload_dict = {
        "pending_bank_rows": int(counts["pending_bank_rows"] or 0),
        "bills_missing_evidence": int(counts["bills_missing_evidence"] or 0),
        "open_close_tasks": int(counts["open_close_tasks"] or 0),
        "locked_periods": int(counts["locked_periods"] or 0),
        "open_periods": int(counts["open_periods"] or 0),
    }
    serialized = json.dumps(payload_dict)
    audit = ToolInvocationAudit(
        tool_name="get_close_readiness_snapshot",
        tool_version=TOOL_VERSIONS["get_close_readiness_snapshot"],
        parameters_hash=_hash_params(params.model_dump()),
        row_count=1,
        result_bytes=len(serialized.encode("utf-8")),
        untrusted_content_sanitized=False,
        duration_ms=max(1, int((time.monotonic() - started) * 1000)),
    )
    return CloseReadinessToolOutput(
        business_id=str(params.business_id),
        **payload_dict,
        audit=audit,
    )


def persist_tool_audits(
    db: Session,
    *,
    business_id: UUID,
    run_id: UUID,
    actor_id: UUID,
    audits: list[ToolInvocationAudit],
) -> None:
    """Persist auditable tool invocation rows (who, what, when, result size) without PII."""
    for a in audits:
        db.execute(
            text(
                """
                INSERT INTO app.ai_tool_audit_logs (
                  id, business_id, run_id, tool_name, parameters_hash,
                  row_count, result_bytes, untrusted_content_sanitized, duration_ms, actor
                ) VALUES (
                  :id, :bid, :rid, :tname, :phash,
                  :rcount, :rbytes, :san, :dur, :actor
                )
                """
            ),
            {
                "id": uuid4(),
                "bid": business_id,
                "rid": run_id,
                "tname": a.tool_name,
                "phash": a.parameters_hash,
                "rcount": a.row_count,
                "rbytes": a.result_bytes,
                "san": a.untrusted_content_sanitized,
                "dur": a.duration_ms,
                "actor": actor_id,
            },
        )
