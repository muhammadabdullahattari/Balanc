"""Typed LangGraph state, finding schema, and immutable dataset registry for financial audits."""

from __future__ import annotations

import operator
import threading
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

AuditMode = Literal[
    "monthly",
    "yearly",
    "emergency",
    "auto_balance_sheet",
    "auto_period_close",
]

TriggerSource = Literal[
    "manual",
    "balance_sheet_generated",
    "period_locked",
    "close_package_prepared",
    "background_job",
]

SeverityLevel = Literal["Critical", "High", "Medium", "Low"]


class AuditFindingModel(BaseModel):
    """Canonical machine-readable schema for every detected audit finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(description="Deterministic finding identifier within the audit run.")
    severity: SeverityLevel = Field(description="Impact severity: Critical, High, Medium, or Low.")
    category: str = Field(
        description="Audit domain category (e.g. balance_sheet, continuity, banking_rule, anomaly)."
    )
    rule_violated: str = Field(description="Canonical rule code or invariant violated.")
    affected_records: list[str] = Field(
        default_factory=list,
        description="IDs or references of journals, accounts, bank rows, or documents affected.",
    )
    expected_vs_actual: dict[str, str] = Field(
        default_factory=dict,
        description="Exact Decimal string values comparing expected vs. observed figures.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Supporting database citations and retrieved RAG policy context.",
    )
    recommendation: str = Field(description="Actionable plain-English remediation step.")


class RetrievedContextChunk(BaseModel):
    """Single RAG context chunk retrieved from tenant policies, rules, and memories."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str
    source_type: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    similarity_score: float = 0.0


@dataclass(frozen=True)
class AuditDataset:
    """Thread-safe, immutable read-only financial snapshot loaded once per audit run."""

    run_id: str
    business_id: str
    business_name: str
    currency: str
    currency_precision: int
    basis: str
    timezone: str
    fiscal_month: int
    jurisdiction: str
    period_start: date
    period_end: date
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    opening_balances: dict[str, Decimal] = field(default_factory=dict)
    period_movements: dict[str, Decimal] = field(default_factory=dict)
    closing_balances: dict[str, Decimal] = field(default_factory=dict)
    journals: list[dict[str, Any]] = field(default_factory=list)
    journal_lines_by_journal: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    saved_balance_sheets: list[dict[str, Any]] = field(default_factory=list)
    computed_balance_sheet: dict[str, Any] = field(default_factory=dict)
    control_tieouts: dict[str, Any] = field(default_factory=dict)
    tax_tieouts: dict[str, Any] = field(default_factory=dict)
    periods: list[dict[str, Any]] = field(default_factory=list)
    close_tasks: list[dict[str, Any]] = field(default_factory=list)
    pending_prepaid_count: int = 0
    bank_rules: list[dict[str, Any]] = field(default_factory=list)
    bank_rows: list[dict[str, Any]] = field(default_factory=list)
    bank_corrections_row_ids: frozenset[str] = field(default_factory=frozenset)
    statements: list[dict[str, Any]] = field(default_factory=list)
    trade_documents: list[dict[str, Any]] = field(default_factory=list)
    parties: dict[str, dict[str, Any]] = field(default_factory=dict)
    payments: list[dict[str, Any]] = field(default_factory=list)
    allocations_by_doc: dict[str, Decimal] = field(default_factory=dict)
    evidence_doc_ids: frozenset[str] = field(default_factory=frozenset)
    all_journal_ids: frozenset[str] = field(default_factory=frozenset)
    posted_journal_ids: frozenset[str] = field(default_factory=frozenset)
    supplier_payment_controls: dict[str, Any] = field(default_factory=dict)
    supplier_beneficiaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    supplier_payments: list[dict[str, Any]] = field(default_factory=list)
    approved_memories: list[dict[str, Any]] = field(default_factory=list)
    policy_history: list[dict[str, Any]] = field(default_factory=list)
    evidence_summaries: list[dict[str, Any]] = field(default_factory=list)


class _DatasetRegistry:
    """Bounded in-process store allowing parallel LangGraph nodes to read AuditDataset safely."""

    def __init__(self, max_entries: int = 64) -> None:
        self._lock = threading.Lock()
        self._max = max_entries
        self._items: dict[str, AuditDataset] = {}

    def put(self, run_id: str, dataset: AuditDataset) -> None:
        with self._lock:
            if len(self._items) >= self._max:
                oldest = next(iter(self._items))
                self._items.pop(oldest, None)
            self._items[run_id] = dataset

    def get(self, run_id: str) -> AuditDataset | None:
        with self._lock:
            return self._items.get(run_id)

    def pop(self, run_id: str) -> None:
        with self._lock:
            self._items.pop(run_id, None)


DATASET_REGISTRY = _DatasetRegistry()


class AuditState(TypedDict, total=False):
    """Typed state flowing through the LangGraph financial audit state machine."""

    run_id: str
    thread_id: str
    business_id: str
    actor_id: str
    audit_type: AuditMode
    trigger_source: TriggerSource
    request_params: dict[str, Any]
    period: dict[str, Any]
    scope_filters: dict[str, Any]
    validation_valid: bool
    has_data: bool
    loaded_data_summary: dict[str, Any]
    retrieved_context: list[dict[str, Any]]
    rule_results: Annotated[list[dict[str, Any]], operator.add]
    candidate_findings: Annotated[list[dict[str, Any]], operator.add]
    findings: list[dict[str, Any]]
    severity_summary: dict[str, Any]
    critical_flagged: bool
    raw_report: dict[str, Any]
    polish_needs_fallback: bool
    polished_report: dict[str, Any]
    errors: Annotated[list[str], operator.add]
    node_trace: Annotated[list[str], operator.add]
    metadata: dict[str, Any]
