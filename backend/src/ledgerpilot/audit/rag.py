"""Tenant-scoped TF-IDF cosine-similarity vector store and RAG retriever for financial audits."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from ledgerpilot.ai.adapter import sanitize_untrusted_text
from ledgerpilot.audit.state import AuditDataset, RetrievedContextChunk

_TOKEN_RE = re.compile(r"[a-z0-9_]{2,}")

_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "are",
        "was",
        "were",
        "must",
        "into",
        "all",
        "any",
        "not",
        "per",
        "has",
        "have",
    }
)

BUILTIN_RULE_DOCS: tuple[dict[str, Any], ...] = (
    {
        "chunk_id": "rule-doc:RULE-BS-001",
        "source_type": "accounting_standard",
        "title": "Accounting Equation & Balance Sheet Tie-Out (RULE-BS-001)",
        "content": (
            "Assets must equal Liabilities plus Equity plus Retained Earnings on every balance "
            "sheet date. Every posted journal entry must have equal total debits and credits, "
            "and saved balance sheet snapshots must match underlying posted journal lines."
        ),
        "tags": ["balance_sheet", "debit_credit", "accounting_equation", "retained_earnings"],
    },
    {
        "chunk_id": "rule-doc:RULE-CONT-001",
        "source_type": "accounting_standard",
        "title": "Opening and Closing Balance Continuity (RULE-CONT-001)",
        "content": (
            "For every active or historical account, Opening Balance plus Net Period Movement "
            "must equal Closing Balance. Adjacent accounting periods must be contiguous without "
            "unexplained balance jumps or uncaptured inter-period entries."
        ),
        "tags": ["continuity", "opening_balance", "closing_balance", "periods"],
    },
    {
        "chunk_id": "rule-doc:RULE-CTRL-001",
        "source_type": "accounting_standard",
        "title": "Subledger to GL Control Account Reconciliation (RULE-CTRL-001)",
        "content": (
            "Accounts Receivable (ar), Accounts Payable (ap), Loan Receivable, Loan Payable, "
            "and Tax control accounts must tie out exactly to open trade documents, loan "
            "principal movements, and tax source lines. Manual journals must not post directly "
            "to subledger control accounts."
        ),
        "tags": ["subledger", "control_account", "ar", "ap", "loans", "tax"],
    },
    {
        "chunk_id": "rule-doc:RULE-BANK-001",
        "source_type": "banking_policy",
        "title": (
            "Bank Categorization Rules, High-Risk Exclusions & Statement Sign-Off (RULE-BANK-001)"
        ),
        "content": (
            "Active bank rules require matching bank descriptions to post to the approved target "
            "account. High-risk keywords (payroll, tax, wire, loan, draw) must not be silently "
            "categorized to plain expense/income without review. Bank rows must not remain "
            "unreviewed past period close, and signed statements must match GL cash balances."
        ),
        "tags": ["banking_rule", "bank_rows", "statement", "reconciliation", "exclusions"],
    },
    {
        "chunk_id": "rule-doc:RULE-DOC-001",
        "source_type": "accounting_standard",
        "title": "Trade Document Integrity, Supporting Evidence & Allocations (RULE-DOC-001)",
        "content": (
            "Invoices and supplier bills require a valid contact role, non-empty document number, "
            "due date on or after issue date, line items plus tax equal to document total, "
            "allocations not exceeding total, and supporting evidence attached to supplier bills."
        ),
        "tags": ["trade_document", "evidence", "allocations", "missing_fields", "references"],
    },
    {
        "chunk_id": "rule-doc:RULE-PAY-001",
        "source_type": "payment_control",
        "title": "Supplier Payment Limits, Dual Approval & Beneficiary Verification (RULE-PAY-001)",
        "content": (
            "Executed supplier payments must respect the single-payment limit, obtain two "
            "independent approvals when at or above the dual-approval threshold, and pay only "
            "out-of-band verified beneficiaries while the payment kill switch is inactive."
        ),
        "tags": ["supplier_payment", "dual_approval", "beneficiary", "limit"],
    },
    {
        "chunk_id": "rule-doc:RULE-LOCK-001",
        "source_type": "close_policy",
        "title": "Locked Period Immutability & Mandatory Close Checklist (RULE-LOCK-001)",
        "content": (
            "Locked accounting periods must have all required close tasks approved by an "
            "independent reviewer, zero overdue unposted prepaid installments, and no late "
            "unreviewed back-dated postings circumventing close sign-off."
        ),
        "tags": ["period_lock", "close_tasks", "prepaids", "segregation"],
    },
    {
        "chunk_id": "rule-doc:RULE-ANOM-001",
        "source_type": "anomaly_policy",
        "title": "Duplicate, Orphaned, Back-Dated & Statistical Outlier Detection (RULE-ANOM-001)",
        "content": (
            "Audit scans flag duplicate bank imports, duplicate supplier bills/invoices, "
            "duplicate manual journals, orphaned cash postings without bank or payment records, "
            "entries back-dated by more than 30 days, and unusually large statistical outliers."
        ),
        "tags": ["anomaly", "duplicate", "orphan", "back_dated", "outlier"],
    },
)


def _tokenize(text: str) -> list[str]:
    return [tok for tok in _TOKEN_RE.findall(text.lower()) if tok not in _STOP_WORDS]


class AuditVectorRetriever:
    """In-memory TF-IDF cosine-similarity vector index scoped to a single tenant audit run."""

    def __init__(self, chunks: list[RetrievedContextChunk]) -> None:
        self._chunks = chunks
        self._doc_tokens: list[Counter[str]] = []
        self._idf: dict[str, float] = {}
        self._vectors: list[dict[str, float]] = []
        self._norms: list[float] = []
        self._build_index()

    def _build_index(self) -> None:
        df: Counter[str] = Counter()
        for chunk in self._chunks:
            combined = f"{chunk.title} {' '.join(chunk.tags)} {chunk.content}"
            counts = Counter(_tokenize(combined))
            self._doc_tokens.append(counts)
            for term in counts:
                df[term] += 1

        total_docs = max(1, len(self._chunks))
        self._idf = {
            term: math.log((1.0 + total_docs) / (1.0 + freq)) + 1.0 for term, freq in df.items()
        }
        for counts in self._doc_tokens:
            vec = {term: (1.0 + math.log(tf)) * self._idf[term] for term, tf in counts.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self._vectors.append(vec)
            self._norms.append(norm)

    def retrieve(self, query: str, *, top_k: int = 8) -> list[RetrievedContextChunk]:
        q_counts = Counter(_tokenize(query))
        if not q_counts or not self._chunks:
            return self._chunks[:top_k]

        q_vec = {
            term: (1.0 + math.log(tf)) * self._idf.get(term, 1.0) for term, tf in q_counts.items()
        }
        q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0

        scored: list[tuple[float, int]] = []
        for idx, doc_vec in enumerate(self._vectors):
            dot = sum(weight * doc_vec.get(term, 0.0) for term, weight in q_vec.items())
            sim = dot / (q_norm * self._norms[idx])
            if sim > 0.0:
                scored.append((round(sim, 4), idx))

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        results: list[RetrievedContextChunk] = []
        for score, idx in scored[:top_k]:
            base = self._chunks[idx]
            results.append(base.model_copy(update={"similarity_score": score}))
        return results


def build_tenant_retriever(dataset: AuditDataset) -> AuditVectorRetriever:
    """Build a sanitized tenant-specific vector index from rules, memories, policies & evidence."""
    chunks: list[RetrievedContextChunk] = [
        RetrievedContextChunk(**doc) for doc in BUILTIN_RULE_DOCS
    ]

    chunks.append(
        RetrievedContextChunk(
            chunk_id=f"workspace-policy:{dataset.business_id}",
            source_type="workspace_policy",
            title=f"Workspace Accounting Policy ({dataset.business_name})",
            content=(
                f"Base currency {dataset.currency} (precision {dataset.currency_precision}), "
                f"accounting basis {dataset.basis}, fiscal start month {dataset.fiscal_month}, "
                f"jurisdiction {dataset.jurisdiction}, timezone {dataset.timezone}."
            ),
            tags=["policy", dataset.basis, dataset.currency.lower()],
        )
    )

    for mem in dataset.approved_memories[:40]:
        title, _ = sanitize_untrusted_text(str(mem.get("title", "")), max_length=120)
        cond, _ = sanitize_untrusted_text(str(mem.get("rule_condition", "")), max_length=300)
        acc_name, _ = sanitize_untrusted_text(str(mem.get("account_name", "")), max_length=120)
        chunks.append(
            RetrievedContextChunk(
                chunk_id=f"memory:{mem['id']}",
                source_type="approved_accounting_memory",
                title=f"Approved Memory: {title} ({mem.get('accounting_framework', 'ALL')})",
                content=(
                    f"Condition: {cond}. Target account {mem.get('account_code', '')} "
                    f"({acc_name}). Materiality threshold: "
                    f"{mem.get('materiality_threshold', '0')} {dataset.currency}."
                ),
                tags=["memory", str(mem.get("category", "policy")), "categorization"],
            )
        )

    for rule in dataset.bank_rules[:50]:
        r_name, _ = sanitize_untrusted_text(str(rule.get("name", "")), max_length=120)
        r_contains, _ = sanitize_untrusted_text(str(rule.get("contains", "")), max_length=120)
        acc = dataset.accounts.get(str(rule.get("account_id")), {})
        chunks.append(
            RetrievedContextChunk(
                chunk_id=f"bank-rule:{rule['id']}",
                source_type="bank_rule",
                title=f"Bank Rule: {r_name} ({'active' if rule.get('active') else 'inactive'})",
                content=(
                    f"Bank transactions containing '{r_contains}' map to account "
                    f"{acc.get('code', '')} {acc.get('name', rule.get('account_id'))} "
                    f"with priority {rule.get('priority', 0)}."
                ),
                tags=["banking_rule", "bank_rows", r_contains.lower()],
            )
        )

    if dataset.supplier_payment_controls:
        ctrl = dataset.supplier_payment_controls
        chunks.append(
            RetrievedContextChunk(
                chunk_id=f"payment-controls:{dataset.business_id}",
                source_type="payment_control",
                title="Workspace Supplier Payment Controls",
                content=(
                    f"Single payment limit: {ctrl.get('single_payment_limit')} {dataset.currency}; "
                    f"dual approval threshold: {ctrl.get('dual_approval_threshold')} "
                    f"{dataset.currency}; kill switch active: {ctrl.get('kill_switch_active')}."
                ),
                tags=["supplier_payment", "limit", "dual_approval"],
            )
        )

    for pol in dataset.policy_history[:20]:
        chunks.append(
            RetrievedContextChunk(
                chunk_id=f"policy-history:v{pol.get('version')}",
                source_type="policy_history",
                title=f"Historical Policy Revision v{pol.get('version')}",
                content=(
                    f"Effective {pol.get('effective_date')}: basis={pol.get('basis')}, "
                    f"jurisdiction={pol.get('jurisdiction')}."
                ),
                tags=["policy", "continuity"],
            )
        )

    for ev in dataset.evidence_summaries[:25]:
        fname, _ = sanitize_untrusted_text(str(ev.get("filename", "")), max_length=120)
        snippet, _ = sanitize_untrusted_text(str(ev.get("snippet", "")), max_length=240)
        chunks.append(
            RetrievedContextChunk(
                chunk_id=f"evidence:{ev['id']}",
                source_type="supporting_evidence",
                title=f"Supporting Document: {fname}",
                content=f"Status {ev.get('status')}. Extracted summary: {snippet or 'N/A'}",
                tags=["evidence", "trade_document"],
            )
        )

    return AuditVectorRetriever(chunks)
