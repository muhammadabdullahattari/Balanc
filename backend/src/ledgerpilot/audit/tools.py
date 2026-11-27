"""LangChain/LangGraph typed tools for RAG retrieval, rule checks, reconciliation, and anomalies."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from ledgerpilot.audit.rag import build_tenant_retriever
from ledgerpilot.audit.state import (
    DATASET_REGISTRY,
    AuditDataset,
    AuditFindingModel,
    SeverityLevel,
)

_HIGH_RISK_BANK_KEYWORDS = ("payroll", "tax", "wire", "loan", "draw")
_NORM_SPACE_RE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _NORM_SPACE_RE.sub(" ", value.strip().lower())


def _finding_id(rule_code: str, records: list[str]) -> str:
    digest = hashlib.sha256(f"{rule_code}|{'|'.join(sorted(records))}".encode()).hexdigest()[:10]
    return f"FIND-{digest.upper()}"


def _make_finding(
    *,
    severity: str,
    category: str,
    rule_violated: str,
    affected_records: list[str],
    expected_vs_actual: dict[str, str],
    evidence: list[str],
    recommendation: str,
) -> dict[str, Any]:
    sev: SeverityLevel = (
        "Critical"
        if severity == "Critical"
        else "High"
        if severity == "High"
        else "Low"
        if severity == "Low"
        else "Medium"
    )
    model = AuditFindingModel(
        id=_finding_id(rule_violated, affected_records),
        severity=sev,
        category=category,
        rule_violated=rule_violated,
        affected_records=affected_records,
        expected_vs_actual=expected_vs_actual,
        evidence=evidence,
        recommendation=recommendation,
    )
    return model.model_dump()


def _require_dataset(run_id: str) -> AuditDataset:
    dataset = DATASET_REGISTRY.get(run_id)
    if dataset is None:
        raise ValueError(f"AuditDataset not found for run_id={run_id}")
    return dataset


class RetrieveContextInput(BaseModel):
    """Input schema for tenant-scoped RAG context retrieval."""

    run_id: str = Field(description="Active audit run identifier.")
    query: str = Field(description="Search query describing audit scope, rules, and categories.")
    top_k: int = Field(default=8, ge=1, le=20, description="Maximum chunks to retrieve.")


class RunScopedToolInput(BaseModel):
    """Input schema for deterministic audit tools operating on the loaded snapshot."""

    run_id: str = Field(description="Active audit run identifier.")


@tool("retrieve_audit_context", args_schema=RetrieveContextInput)
def retrieve_audit_context_tool(run_id: str, query: str, top_k: int = 8) -> dict[str, Any]:
    """Retrieve tenant accounting policies, approved memories, bank rules, and rule docs via RAG."""
    dataset = _require_dataset(run_id)
    retriever = build_tenant_retriever(dataset)
    chunks = retriever.retrieve(query, top_k=top_k)
    return {
        "tool": "retrieve_audit_context",
        "chunk_count": len(chunks),
        "chunks": [c.model_dump() for c in chunks],
    }


@tool("evaluate_banking_rules", args_schema=RunScopedToolInput)
def evaluate_banking_rules_tool(run_id: str) -> dict[str, Any]:
    """Evaluate active bank rules, high-risk keyword exclusions, unreviewed rows & statements."""
    ds = _require_dataset(run_id)
    findings: list[dict[str, Any]] = []
    active_rules = [r for r in ds.bank_rules if r.get("active")]

    # Map journal_id -> non-bank posted account_ids
    journal_non_bank_accounts: dict[str, set[str]] = {}
    for jid, lines in ds.journal_lines_by_journal.items():
        journal_non_bank_accounts[jid] = {str(ln["account_id"]) for ln in lines}

    unreviewed_ids: list[str] = []
    for row in ds.bank_rows:
        rid = str(row["id"])
        state = str(row["state"])
        desc = str(row["description"])
        desc_lower = desc.lower()
        amt = Decimal(str(row["amount"]))

        if state == "for_review":
            unreviewed_ids.append(rid)
            continue

        row_jid = row.get("journal_id")
        posted_accounts = (
            journal_non_bank_accounts.get(str(row_jid), set()) - {str(row["account_id"])}
            if row_jid
            else set()
        )

        # 1. Active bank rule compliance check (mirrors banking/shadow.py invariant)
        if state == "categorized" and rid not in ds.bank_corrections_row_ids and posted_accounts:
            for rule in active_rules:
                needle = str(rule["contains"]).lower()
                target_id = str(rule["account_id"])
                target_acc = ds.accounts.get(target_id, {})
                target_kind = target_acc.get("kind")
                sign_matches = (target_kind == "income" and amt > 0) or (
                    target_kind == "expense" and amt < 0
                )
                if (
                    needle
                    and needle in desc_lower
                    and sign_matches
                    and target_id not in posted_accounts
                ):
                    actual_names = [
                        ds.accounts.get(aid, {}).get("code", aid) for aid in sorted(posted_accounts)
                    ]
                    findings.append(
                        _make_finding(
                            severity="High",
                            category="banking_rule",
                            rule_violated="RULE-BANK-001:BANK_RULE_MISMATCH",
                            affected_records=[rid, str(rule["id"])],
                            expected_vs_actual={
                                "expected_account": f"{target_acc.get('code', target_id)}",
                                "actual_account": ",".join(actual_names),
                                "amount": str(amt),
                            },
                            evidence=[
                                f"Bank row {rid} ('{desc}', {amt} {ds.currency}) matched active "
                                f"rule '{rule['name']}' (contains '{rule['contains']}'), "
                                f"but was categorized to {','.join(actual_names)}."
                            ],
                            recommendation=(
                                "Review the bank categorization or record a bank correction so it "
                                "aligns with the approved bank rule."
                            ),
                        )
                    )
                    break

        # 2. High-risk restricted keyword check (payroll, tax, wire, loan, draw)
        matched_kw = [kw for kw in _HIGH_RISK_BANK_KEYWORDS if kw in desc_lower]
        if state == "categorized" and matched_kw and posted_accounts:
            plain_only = all(
                ds.accounts.get(aid, {}).get("control") is None
                and ds.accounts.get(aid, {}).get("kind") in {"income", "expense"}
                for aid in posted_accounts
            )
            if plain_only and rid not in ds.bank_corrections_row_ids:
                findings.append(
                    _make_finding(
                        severity="Medium",
                        category="banking_rule",
                        rule_violated="RULE-BANK-001:HIGH_RISK_KEYWORD_CATEGORIZED",
                        affected_records=[rid],
                        expected_vs_actual={
                            "expected_treatment": "subledger_or_reviewed_split",
                            "actual_treatment": "plain_category",
                            "amount": str(amt),
                        },
                        evidence=[
                            f"Bank row {rid} ('{desc}') contains restricted keyword(s) "
                            f"{','.join(matched_kw)} and was posted directly to a plain category."
                        ],
                        recommendation=(
                            "Verify whether this transaction represents a loan, tax remittance, "
                            "owner draw, or payroll clearing movement requiring subledger linkage."
                        ),
                    )
                )

    if unreviewed_ids:
        findings.append(
            _make_finding(
                severity="Medium" if len(unreviewed_ids) < 10 else "High",
                category="banking_rule",
                rule_violated="RULE-BANK-001:UNREVIEWED_BANK_ROWS",
                affected_records=unreviewed_ids[:25],
                expected_vs_actual={
                    "expected_unreviewed_rows": "0",
                    "actual_unreviewed_rows": str(len(unreviewed_ids)),
                },
                evidence=[
                    f"{len(unreviewed_ids)} imported bank transaction(s) in the audit period "
                    "remain in 'for_review' status and are excluded from the general ledger."
                ],
                recommendation=(
                    "Categorize, match, or explicitly exclude all pending bank transactions "
                    "before finalizing financial statements."
                ),
            )
        )

    # 3. Signed bank statement consistency check
    for stmt in ds.statements:
        closing = Decimal(str(stmt["closing_balance"]))
        ledger_bal = Decimal(str(stmt["ledger_balance"]))
        out_total = Decimal("0")
        for item in stmt.get("outstanding") or []:
            if isinstance(item, dict) and "amount" in item:
                out_total += Decimal(str(item["amount"]))
        diff = ledger_bal - closing - out_total
        if out_total == 0 and ledger_bal != closing and stmt.get("outstanding"):
            # Outstanding items store journal_id + position; look up if available
            computed_out = Decimal("0")
            known_all = True
            for item in stmt["outstanding"]:
                jid = str(item.get("journal_id", ""))
                pos = item.get("position")
                matched_lines = [
                    ln
                    for ln in ds.journal_lines_by_journal.get(jid, [])
                    if ln["position"] == pos and ln["account_id"] == stmt["account_id"]
                ]
                if matched_lines:
                    computed_out += Decimal(matched_lines[0]["debit"]) - Decimal(
                        matched_lines[0]["credit"]
                    )
                else:
                    known_all = False
            diff = ledger_bal - closing - computed_out if known_all else Decimal("0")
        elif not stmt.get("outstanding"):
            diff = ledger_bal - closing

        if diff != 0:
            findings.append(
                _make_finding(
                    severity="High",
                    category="banking_rule",
                    rule_violated="RULE-BANK-001:STATEMENT_DISCREPANCY",
                    affected_records=[str(stmt["id"]), str(stmt["account_id"])],
                    expected_vs_actual={
                        "expected_difference": "0",
                        "actual_difference": str(diff),
                        "statement_closing": str(closing),
                        "ledger_balance": str(ledger_bal),
                    },
                    evidence=[
                        f"Signed statement {stmt['id']} as of {stmt['as_of']} has an unexplained "
                        f"variance of {diff} {ds.currency} against its recorded ledger balance."
                    ],
                    recommendation=(
                        "Re-verify outstanding timing items and bank reconciliations for this "
                        "account as of the statement date."
                    ),
                )
            )

    return {
        "tool": "evaluate_banking_rules",
        "evaluated_bank_rows": len(ds.bank_rows),
        "active_rules_count": len(active_rules),
        "findings": findings,
    }


@tool("evaluate_accounting_rules", args_schema=RunScopedToolInput)
def evaluate_accounting_rules_tool(run_id: str) -> dict[str, Any]:
    """Evaluate accounting rules across journals, accounts, trade documents, and references."""
    ds = _require_dataset(run_id)
    findings: list[dict[str, Any]] = []

    locked_ranges = [
        (date.fromisoformat(p["start_date"]), date.fromisoformat(p["end_date"]), str(p["id"]))
        for p in ds.periods
        if p.get("locked")
    ]

    for j in ds.journals:
        jid = str(j["id"])
        j_status = str(j["status"])
        j_date = date.fromisoformat(str(j["accounting_date"]))
        lines = ds.journal_lines_by_journal.get(jid, [])

        if j_status == "posted" and not lines:
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="accounting_rule",
                    rule_violated="RULE-ACC-001:EMPTY_POSTED_JOURNAL",
                    affected_records=[jid],
                    expected_vs_actual={"expected_min_lines": "2", "actual_lines": "0"},
                    evidence=[f"Posted journal {jid} on {j_date} has zero journal lines."],
                    recommendation="Investigate ledger integrity and reverse or reconstruct lines.",
                )
            )

        if j_status == "posted" and not str(j.get("memo") or "").strip():
            findings.append(
                _make_finding(
                    severity="Low",
                    category="missing_fields",
                    rule_violated="RULE-ACC-002:MISSING_JOURNAL_MEMO",
                    affected_records=[jid],
                    expected_vs_actual={"expected_memo": "non_empty", "actual_memo": "empty"},
                    evidence=[f"Posted journal {jid} on {j_date} has a blank memo/narration."],
                    recommendation="Document business purpose via an annotation or reversal memo.",
                )
            )

        # Check postings to inactive or missing accounts & manual control-account bypass
        for ln in lines:
            aid = str(ln["account_id"])
            acc = ds.accounts.get(aid)
            if acc is None:
                findings.append(
                    _make_finding(
                        severity="Critical",
                        category="broken_reference",
                        rule_violated="RULE-REF-001:MISSING_ACCOUNT_REFERENCE",
                        affected_records=[jid, aid],
                        expected_vs_actual={"expected_account": "valid_account", "actual": aid},
                        evidence=[
                            f"Journal {jid} line {ln['position']} references missing account {aid}."
                        ],
                        recommendation="Restore the missing chart-of-accounts record or reverse.",
                    )
                )
                continue

            if j_status == "posted" and not acc.get("active", True):
                findings.append(
                    _make_finding(
                        severity="High",
                        category="accounting_rule",
                        rule_violated="RULE-ACC-003:POSTED_TO_INACTIVE_ACCOUNT",
                        affected_records=[jid, aid],
                        expected_vs_actual={
                            "expected_account_active": "true",
                            "actual_account_active": "false",
                        },
                        evidence=[
                            f"Posted journal {jid} on {j_date} recorded activity on inactive "
                            f"account {acc['code']} ({acc['name']})."
                        ],
                        recommendation=(
                            "Reclassify the entry to an active account or reactivate the account "
                            "if still in operational use."
                        ),
                    )
                )

            if (
                j_status == "posted"
                and j.get("source_kind") == "manual_journal"
                and not j.get("reversal_of")
                and acc.get("control") in {"ar", "ap", "loan_payable", "loan_receivable", "tax"}
            ):
                amt = Decimal(str(ln["debit"])) - Decimal(str(ln["credit"]))
                findings.append(
                    _make_finding(
                        severity="High",
                        category="accounting_rule",
                        rule_violated="RULE-CTRL-001:MANUAL_CONTROL_ACCOUNT_BYPASS",
                        affected_records=[jid, aid],
                        expected_vs_actual={
                            "expected_source": "subledger_document_or_payment",
                            "actual_source": "manual_journal",
                            "net_entry": str(amt),
                        },
                        evidence=[
                            f"Manual journal {jid} posted {amt} {ds.currency} directly to "
                            f"control account {acc['code']} ({acc['control']}), risking "
                            "subledger-to-GL tie-out drift."
                        ],
                        recommendation=(
                            "Reverse the manual journal on the control account and record the "
                            "change through the proper invoice, bill, loan, or tax workflow."
                        ),
                    )
                )

        # Check posted journal created after a locked period closed
        created_raw = j.get("created_at")
        if j_status == "posted" and created_raw:
            created_dt = date.fromisoformat(str(created_raw))
            for p_start, p_end, pid in locked_ranges:
                if p_start <= j_date <= p_end and created_dt > p_end:
                    findings.append(
                        _make_finding(
                            severity="Medium",
                            category="close_policy",
                            rule_violated="RULE-LOCK-001:POST_PERIOD_ENTRY_IN_LOCKED_RANGE",
                            affected_records=[jid, pid],
                            expected_vs_actual={
                                "period_end": p_end.isoformat(),
                                "entry_created_date": created_dt.isoformat(),
                            },
                            evidence=[
                                f"Journal {jid} dated {j_date} inside now-locked period {pid} "
                                f"was created after period end ({created_dt})."
                            ],
                            recommendation=(
                                "Confirm close workpapers captured this late adjustment before "
                                "the period was locked."
                            ),
                        )
                    )

    # Check trade documents: mandatory fields, party role, line sum, evidence, allocations, refs
    bills_missing_evidence: list[str] = []
    for doc in ds.trade_documents:
        did = str(doc["id"])
        kind = str(doc["kind"])
        total = Decimal(str(doc["total"]))
        tax = Decimal(str(doc["tax"]))
        issue_dt = date.fromisoformat(str(doc["accounting_date"]))
        due_dt = date.fromisoformat(str(doc["due_date"]))

        if not str(doc.get("number") or "").strip():
            findings.append(
                _make_finding(
                    severity="Medium",
                    category="missing_fields",
                    rule_violated="RULE-DOC-001:MISSING_DOCUMENT_NUMBER",
                    affected_records=[did],
                    expected_vs_actual={"expected_number": "non_empty", "actual_number": "empty"},
                    evidence=[f"Trade document {did} ({kind}) has an empty reference number."],
                    recommendation="Assign a unique document reference number.",
                )
            )

        if due_dt < issue_dt:
            findings.append(
                _make_finding(
                    severity="Medium",
                    category="accounting_rule",
                    rule_violated="RULE-DOC-001:DUE_DATE_BEFORE_ISSUE_DATE",
                    affected_records=[did],
                    expected_vs_actual={
                        "issue_date": issue_dt.isoformat(),
                        "due_date": due_dt.isoformat(),
                    },
                    evidence=[
                        f"{kind.title()} {doc['number']} ({did}) has due date {due_dt} earlier "
                        f"than accounting date {issue_dt}."
                    ],
                    recommendation="Correct the document due date or issue a credit/reissue.",
                )
            )

        party = ds.parties.get(str(doc["party_id"]))
        expected_role = (
            "customer" if kind in {"invoice", "sales_receipt", "customer_credit"} else "supplier"
        )
        if party is None or expected_role not in (party.get("roles") or []):
            findings.append(
                _make_finding(
                    severity="High",
                    category="broken_reference",
                    rule_violated="RULE-DOC-001:INVALID_PARTY_OR_ROLE",
                    affected_records=[did, str(doc["party_id"])],
                    expected_vs_actual={
                        "expected_role": expected_role,
                        "actual_roles": ",".join(party.get("roles", [])) if party else "missing",
                    },
                    evidence=[
                        f"{kind.title()} {doc['number']} ({did}) references contact "
                        f"{doc['party_id']} without '{expected_role}' role."
                    ],
                    recommendation="Update the contact's relationship roles or reissue cleanly.",
                )
            )

        line_sum = Decimal("0")
        line_tax_sum = Decimal("0")
        for ln in doc.get("lines") or []:
            line_sum += Decimal(str(ln.get("amount", "0")))
            line_tax_sum += Decimal(str(ln.get("tax_amount", "0")))
        if (line_sum + line_tax_sum != total) or (line_tax_sum != tax):
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="accounting_rule",
                    rule_violated="RULE-DOC-001:DOCUMENT_TOTAL_MISMATCH",
                    affected_records=[did],
                    expected_vs_actual={
                        "expected_total": str(line_sum + line_tax_sum),
                        "actual_total": str(total),
                        "expected_tax": str(line_tax_sum),
                        "actual_tax": str(tax),
                    },
                    evidence=[
                        f"{kind.title()} {doc['number']} ({did}) header total ({total}) or tax "
                        f"({tax}) disagrees with line items ({line_sum} + {line_tax_sum})."
                    ],
                    recommendation="Correct or reissue the document so header and lines match.",
                )
            )

        if str(doc["journal_id"]) not in ds.posted_journal_ids:
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="broken_reference",
                    rule_violated="RULE-REF-002:DOCUMENT_JOURNAL_UNPOSTED_OR_MISSING",
                    affected_records=[did, str(doc["journal_id"])],
                    expected_vs_actual={
                        "expected_journal_status": "posted",
                        "actual_journal_id": str(doc["journal_id"]),
                    },
                    evidence=[
                        f"{kind.title()} {doc['number']} ({did}) points to journal "
                        f"{doc['journal_id']} which is missing or not posted."
                    ],
                    recommendation="Investigate missing posting linkage immediately.",
                )
            )

        allocated = ds.allocations_by_doc.get(did, Decimal("0"))
        if kind in {"invoice", "bill"} and allocated > total:
            findings.append(
                _make_finding(
                    severity="High",
                    category="accounting_rule",
                    rule_violated="RULE-DOC-001:OVER_ALLOCATED_DOCUMENT",
                    affected_records=[did],
                    expected_vs_actual={
                        "document_total": str(total),
                        "allocated_amount": str(allocated),
                    },
                    evidence=[
                        f"{kind.title()} {doc['number']} ({did}) has {allocated} {ds.currency} "
                        f"allocated against a total of {total} {ds.currency}."
                    ],
                    recommendation=(
                        "Reallocate excess payment or record a customer/supplier advance."
                    ),
                )
            )

        if kind == "bill" and did not in ds.evidence_doc_ids:
            bills_missing_evidence.append(f"{doc['number']} ({did})")

    if bills_missing_evidence:
        findings.append(
            _make_finding(
                severity="Low" if len(bills_missing_evidence) <= 3 else "Medium",
                category="missing_fields",
                rule_violated="RULE-DOC-001:BILLS_WITHOUT_EVIDENCE",
                affected_records=bills_missing_evidence[:20],
                expected_vs_actual={
                    "expected_missing_evidence_bills": "0",
                    "actual_missing_evidence_bills": str(len(bills_missing_evidence)),
                },
                evidence=[
                    f"{len(bills_missing_evidence)} supplier bill(s) in the period do not have "
                    "supporting invoice/receipt files linked."
                ],
                recommendation=(
                    "Upload and link supplier invoice PDFs or receipts in Supporting Documents."
                ),
            )
        )

    # Check broken journal references on payments and settled bank rows
    for pay in ds.payments:
        pid = str(pay["id"])
        if str(pay["journal_id"]) not in ds.posted_journal_ids:
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="broken_reference",
                    rule_violated="RULE-REF-003:PAYMENT_JOURNAL_MISSING",
                    affected_records=[pid, str(pay["journal_id"])],
                    expected_vs_actual={
                        "expected_journal_status": "posted",
                        "actual_journal_id": str(pay["journal_id"]),
                    },
                    evidence=[
                        f"Payment {pid} ({pay['total']} {ds.currency}) references missing or "
                        f"unposted journal {pay['journal_id']}."
                    ],
                    recommendation="Reconcile payment subledger against posted cash journals.",
                )
            )

    for brow in ds.bank_rows:
        if (
            brow["state"] in {"categorized", "transferred", "split"}
            and brow.get("journal_id")
            and str(brow["journal_id"]) not in ds.posted_journal_ids
        ):
            findings.append(
                _make_finding(
                    severity="High",
                    category="broken_reference",
                    rule_violated="RULE-REF-004:BANK_ROW_JOURNAL_MISSING",
                    affected_records=[str(brow["id"]), str(brow["journal_id"])],
                    expected_vs_actual={
                        "expected_journal_status": "posted",
                        "actual_journal_id": str(brow["journal_id"]),
                    },
                    evidence=[
                        f"Reviewed bank row {brow['id']} references missing or unposted "
                        f"journal {brow['journal_id']}."
                    ],
                    recommendation="Re-link or re-categorize the bank row.",
                )
            )

    return {
        "tool": "evaluate_accounting_rules",
        "evaluated_journals": len(ds.journals),
        "evaluated_documents": len(ds.trade_documents),
        "findings": findings,
    }


@tool("evaluate_payment_and_close_rules", args_schema=RunScopedToolInput)
def evaluate_payment_and_close_rules_tool(run_id: str) -> dict[str, Any]:
    """Evaluate supplier payment limits/approvals, close-task segregation & prepaid schedules."""
    ds = _require_dataset(run_id)
    findings: list[dict[str, Any]] = []

    ctrl = ds.supplier_payment_controls
    single_limit = Decimal(str(ctrl["single_payment_limit"])) if ctrl else None
    dual_thresh = Decimal(str(ctrl["dual_approval_threshold"])) if ctrl else None

    for sp in ds.supplier_payments:
        sp_id = str(sp["id"])
        amt = Decimal(str(sp["amount"]))
        state = str(sp["state"])
        if state in {"rejected", "invalidated", "failed"}:
            continue

        if single_limit is not None and amt > single_limit:
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="payment_control",
                    rule_violated="RULE-PAY-001:SINGLE_PAYMENT_LIMIT_EXCEEDED",
                    affected_records=[sp_id],
                    expected_vs_actual={
                        "single_payment_limit": str(single_limit),
                        "actual_payment_amount": str(amt),
                    },
                    evidence=[
                        f"Supplier payment {sp_id} (bill {sp['bill_reference']}) for {amt} "
                        f"{ds.currency} exceeds single-payment cap {single_limit} {ds.currency}."
                    ],
                    recommendation="Hold or split the payout and obtain executive sign-off.",
                )
            )

        if (
            state in {"executed", "settled_posted"}
            and dual_thresh is not None
            and amt >= dual_thresh
        ):
            a1 = sp.get("first_approved_by")
            a2 = sp.get("final_approved_by")
            if not a1 or not a2 or a1 == a2:
                findings.append(
                    _make_finding(
                        severity="High",
                        category="payment_control",
                        rule_violated="RULE-PAY-001:MISSING_DUAL_APPROVAL",
                        affected_records=[sp_id],
                        expected_vs_actual={
                            "dual_approval_threshold": str(dual_thresh),
                            "distinct_approvers_count": str(len({x for x in (a1, a2) if x})),
                        },
                        evidence=[
                            f"Executed supplier payment {sp_id} ({amt} {ds.currency}) met or "
                            f"exceeded dual-approval threshold {dual_thresh} without two "
                            "distinct approvers."
                        ],
                        recommendation="Enforce two-person approval on high-value disbursements.",
                    )
                )

        ben = ds.supplier_beneficiaries.get(str(sp["beneficiary_id"]))
        if state in {"executed", "settled_posted"} and (
            ben is None or not ben.get("out_of_band_verified")
        ):
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="payment_control",
                    rule_violated="RULE-PAY-001:UNVERIFIED_BENEFICIARY_PAID",
                    affected_records=[sp_id, str(sp["beneficiary_id"])],
                    expected_vs_actual={
                        "expected_beneficiary_verified": "true",
                        "actual_beneficiary_verified": "false",
                    },
                    evidence=[
                        f"Supplier payment {sp_id} was executed to beneficiary "
                        f"{sp['beneficiary_id']} without out-of-band bank verification."
                    ],
                    recommendation="Verify supplier bank details out-of-band immediately.",
                )
            )

    # Close task & segregation rules for periods overlapping the audit window
    locked_period_ids = {
        str(p["id"])
        for p in ds.periods
        if p.get("locked")
        and date.fromisoformat(p["start_date"]) <= ds.period_end
        and date.fromisoformat(p["end_date"]) >= ds.period_start
    }
    for task in ds.close_tasks:
        tid = str(task["id"])
        pid = str(task["period_id"])
        if task.get("prepared_by") and task.get("prepared_by") == task.get("reviewed_by"):
            findings.append(
                _make_finding(
                    severity="High",
                    category="close_policy",
                    rule_violated="RULE-LOCK-001:CLOSE_TASK_SELF_REVIEW",
                    affected_records=[tid, pid],
                    expected_vs_actual={
                        "expected_reviewer": "distinct_from_preparer",
                        "actual_reviewer": "same_as_preparer",
                    },
                    evidence=[
                        f"Close task '{task['name']}' ({tid}) was prepared and reviewed "
                        "by the same user."
                    ],
                    recommendation="Reassign review to an independent accountant or owner.",
                )
            )
        if pid in locked_period_ids and task.get("required") and task.get("state") != "approved":
            findings.append(
                _make_finding(
                    severity="High",
                    category="close_policy",
                    rule_violated="RULE-LOCK-001:LOCKED_PERIOD_UNAPPROVED_TASK",
                    affected_records=[tid, pid],
                    expected_vs_actual={
                        "expected_task_state": "approved",
                        "actual_task_state": str(task.get("state")),
                    },
                    evidence=[
                        f"Locked period {pid} still has required close task '{task['name']}' "
                        f"in state '{task.get('state')}'."
                    ],
                    recommendation="Reopen period or complete independent close task sign-off.",
                )
            )

    if ds.pending_prepaid_count > 0:
        findings.append(
            _make_finding(
                severity="Medium",
                category="close_policy",
                rule_violated="RULE-LOCK-001:PENDING_PREPAID_INSTALLMENTS",
                affected_records=[f"prepaids-as-of-{ds.period_end.isoformat()}"],
                expected_vs_actual={
                    "expected_pending_installments": "0",
                    "actual_pending_installments": str(ds.pending_prepaid_count),
                },
                evidence=[
                    f"{ds.pending_prepaid_count} scheduled prepaid expense installment(s) due "
                    f"on or before {ds.period_end} have not yet been recognized."
                ],
                recommendation="Review and post due prepaid amortization installments.",
            )
        )

    return {
        "tool": "evaluate_payment_and_close_rules",
        "evaluated_supplier_payments": len(ds.supplier_payments),
        "evaluated_close_tasks": len(ds.close_tasks),
        "findings": findings,
    }


@tool("reconcile_balances_and_continuity", args_schema=RunScopedToolInput)
def reconcile_balances_and_continuity_tool(run_id: str) -> dict[str, Any]:
    """Verify debit=credit, accounting equation, balance sheet drift, continuity, and tie-outs."""
    ds = _require_dataset(run_id)
    findings: list[dict[str, Any]] = []

    with localcontext() as ctx:
        ctx.prec = 60

        # 1. Per-journal debit == credit check
        for j in ds.journals:
            if j["status"] != "posted":
                continue
            jid = str(j["id"])
            lines = ds.journal_lines_by_journal.get(jid, [])
            deb = sum((Decimal(str(ln["debit"])) for ln in lines), Decimal("0"))
            cred = sum((Decimal(str(ln["credit"])) for ln in lines), Decimal("0"))
            if deb != cred:
                findings.append(
                    _make_finding(
                        severity="Critical",
                        category="balance_sheet",
                        rule_violated="RULE-BS-001:JOURNAL_DEBIT_CREDIT_IMBALANCE",
                        affected_records=[jid],
                        expected_vs_actual={
                            "total_debit": str(deb),
                            "total_credit": str(cred),
                            "imbalance": str(deb - cred),
                        },
                        evidence=[
                            f"Posted journal {jid} on {j['accounting_date']} is out of balance: "
                            f"debits={deb} vs credits={cred} ({ds.currency})."
                        ],
                        recommendation="Halt reporting and correct the unbalanced journal lines.",
                    )
                )

        # 2. Accounting Equation: Assets == Liabilities + Equity + Retained Earnings
        assets = Decimal("0")
        liabilities = Decimal("0")
        equity = Decimal("0")
        income_net = Decimal("0")
        expense_net = Decimal("0")
        for aid, acc in ds.accounts.items():
            bal = ds.closing_balances.get(aid, Decimal("0"))
            kind = acc["kind"]
            if kind == "asset":
                assets += bal
            elif kind == "liability":
                liabilities += -bal
            elif kind == "equity":
                equity += -bal
            elif kind == "income":
                income_net += -bal
            elif kind == "expense":
                expense_net += bal

        retained_earnings = income_net - expense_net
        rhs = liabilities + equity + retained_earnings
        eq_diff = assets - rhs
        if eq_diff != 0 or not bool(ds.computed_balance_sheet.get("balanced", True)):
            findings.append(
                _make_finding(
                    severity="Critical",
                    category="balance_sheet",
                    rule_violated="RULE-BS-001:ACCOUNTING_EQUATION_IMBALANCE",
                    affected_records=[f"balance-sheet:{ds.period_end.isoformat()}"],
                    expected_vs_actual={
                        "assets": str(assets),
                        "liabilities_plus_equity_plus_retained": str(rhs),
                        "difference": str(eq_diff),
                    },
                    evidence=[
                        f"As of {ds.period_end}, Assets ({assets}) != Liabilities ({liabilities}) "
                        f"+ Equity ({equity}) + Retained Earnings ({retained_earnings})."
                    ],
                    recommendation="Inspect trial balance and unlinked journal lines immediately.",
                )
            )

        # 3. Saved Balance Sheet snapshots vs underlying transactions consistency
        computed_by_acc = {
            str(item["account_id"]): Decimal(str(item["amount"]))
            for item in (ds.computed_balance_sheet.get("items") or [])
        }
        for snap in ds.saved_balance_sheets:
            if not snap.get("balanced", True):
                findings.append(
                    _make_finding(
                        severity="Critical",
                        category="balance_sheet",
                        rule_violated="RULE-BS-001:SAVED_BALANCE_SHEET_UNBALANCED",
                        affected_records=[str(snap["report_id"])],
                        expected_vs_actual={
                            "expected_balanced": "true",
                            "actual_balanced": "false",
                        },
                        evidence=[
                            f"Saved balance sheet {snap['report_id']} (as of {snap['as_of']}) "
                            "was recorded in an unbalanced state."
                        ],
                        recommendation="Regenerate the balance sheet after fixing ledger entries.",
                    )
                )
            if snap.get("as_of") == ds.period_end.isoformat() and snap.get("basis") == "accrual":
                for item in snap.get("items") or []:
                    aid = str(item["account_id"])
                    snap_amt = Decimal(str(item["amount"]))
                    live_amt = computed_by_acc.get(aid, Decimal("0"))
                    if snap_amt != live_amt:
                        findings.append(
                            _make_finding(
                                severity="High",
                                category="balance_sheet",
                                rule_violated="RULE-BS-001:SAVED_BALANCE_SHEET_DRIFT",
                                affected_records=[str(snap["report_id"]), aid],
                                expected_vs_actual={
                                    "underlying_ledger_amount": str(live_amt),
                                    "saved_balance_sheet_amount": str(snap_amt),
                                    "variance": str(live_amt - snap_amt),
                                },
                                evidence=[
                                    f"Saved balance sheet {snap['report_id']} as of "
                                    f"{snap['as_of']} shows {snap_amt} for account "
                                    f"{item.get('code', aid)}, but underlying posted transactions "
                                    f"sum to {live_amt}."
                                ],
                                recommendation=(
                                    "Regenerate the balance sheet snapshot so it reflects all "
                                    "subsequently posted entries."
                                ),
                            )
                        )

        # 4. Opening + Movement == Closing Continuity per account
        all_aids = (
            set(ds.accounts)
            | set(ds.opening_balances)
            | set(ds.period_movements)
            | set(ds.closing_balances)
        )
        for aid in sorted(all_aids):
            op_bal = ds.opening_balances.get(aid, Decimal("0"))
            mov = ds.period_movements.get(aid, Decimal("0"))
            cl_bal = ds.closing_balances.get(aid, Decimal("0"))
            if op_bal + mov != cl_bal:
                acc_code = ds.accounts.get(aid, {}).get("code", aid)
                findings.append(
                    _make_finding(
                        severity="Critical",
                        category="continuity",
                        rule_violated="RULE-CONT-001:OPENING_CLOSING_CONTINUITY_BREAK",
                        affected_records=[aid],
                        expected_vs_actual={
                            "opening_plus_movement": str(op_bal + mov),
                            "closing_balance": str(cl_bal),
                            "difference": str(cl_bal - (op_bal + mov)),
                        },
                        evidence=[
                            f"Account {acc_code} opening ({op_bal}) + period movement ({mov}) "
                            f"!= closing ({cl_bal})."
                        ],
                        recommendation=(
                            "Rebuild balance summaries and inspect period cutover dates."
                        ),
                    )
                )

        # 5. Period-to-period gap continuity check
        sorted_periods = sorted(ds.periods, key=lambda p: str(p["start_date"]))
        for idx in range(1, len(sorted_periods)):
            prev_end = date.fromisoformat(sorted_periods[idx - 1]["end_date"])
            curr_start = date.fromisoformat(sorted_periods[idx]["start_date"])
            gap_days = (curr_start - prev_end).days - 1
            if gap_days > 0:
                gap_journals = [
                    str(j["id"])
                    for j in ds.journals
                    if j["status"] == "posted"
                    and prev_end < date.fromisoformat(j["accounting_date"]) < curr_start
                ]
                findings.append(
                    _make_finding(
                        severity="High" if gap_journals else "Medium",
                        category="continuity",
                        rule_violated="RULE-CONT-001:PERIOD_GAP_DISCONTINUITY",
                        affected_records=[
                            str(sorted_periods[idx - 1]["id"]),
                            str(sorted_periods[idx]["id"]),
                            *gap_journals[:10],
                        ],
                        expected_vs_actual={
                            "expected_gap_days": "0",
                            "actual_gap_days": str(gap_days),
                            "uncovered_posted_journals": str(len(gap_journals)),
                        },
                        evidence=[
                            f"Gap of {gap_days} day(s) detected between period ending {prev_end} "
                            f"and next period starting {curr_start} "
                            f"({len(gap_journals)} posted journal(s) in gap)."
                        ],
                        recommendation=(
                            "Create contiguous accounting periods so opening and closing period "
                            "balances roll forward seamlessly."
                        ),
                    )
                )

        # 6. Subledger (AR, AP, Loans) & Tax Control Tie-Outs
        for item in ds.control_tieouts.get("items") or []:
            if not item.get("balanced", True):
                aid = str(item["account_id"])
                acc = ds.accounts.get(aid, {})
                findings.append(
                    _make_finding(
                        severity="Critical",
                        category="subledger_reconciliation",
                        rule_violated="RULE-CTRL-001:SUBLEDGER_CONTROL_MISMATCH",
                        affected_records=[aid],
                        expected_vs_actual={
                            "subledger_balance": str(item["subledger_balance"]),
                            "ledger_balance": str(item["ledger_balance"]),
                            "difference": str(item["difference"]),
                        },
                        evidence=[
                            f"Control account {acc.get('code', aid)} ({item['control']}) GL "
                            f"balance ({item['ledger_balance']}) differs from open subledger "
                            f"items ({item['subledger_balance']}) by {item['difference']}."
                        ],
                        recommendation=(
                            "Trace unlinked entries on the control account and reconcile open "
                            "invoices, bills, or loan schedules."
                        ),
                    )
                )

        for tax_item in ds.tax_tieouts.get("items") or []:
            diff = Decimal(str(tax_item.get("difference", "0")))
            if diff != 0:
                aid = str(tax_item["account_id"])
                findings.append(
                    _make_finding(
                        severity="High",
                        category="subledger_reconciliation",
                        rule_violated="RULE-CTRL-001:TAX_CONTROL_MISMATCH",
                        affected_records=[aid],
                        expected_vs_actual={
                            "tax_source_balance": str(tax_item["source_balance"]),
                            "tax_ledger_balance": str(tax_item["closing_debit_minus_credit"]),
                            "difference": str(diff),
                        },
                        evidence=[
                            f"Tax control account '{tax_item['name']}' ledger balance "
                            f"({tax_item['closing_debit_minus_credit']}) differs from tax "
                            f"document/remittance sources ({tax_item['source_balance']}) by {diff}."
                        ],
                        recommendation=(
                            "Reconcile tax lines on issued documents and tax remittances against "
                            "the tax control account."
                        ),
                    )
                )

    return {
        "tool": "reconcile_balances_and_continuity",
        "assets": str(assets),
        "liabilities": str(liabilities),
        "equity": str(equity),
        "retained_earnings": str(retained_earnings),
        "findings": findings,
    }


@tool("detect_ledger_and_bank_anomalies", args_schema=RunScopedToolInput)
def detect_ledger_and_bank_anomalies_tool(run_id: str) -> dict[str, Any]:
    """Detect duplicates, orphaned cash postings, back-dated entries, and statistical outliers."""
    ds = _require_dataset(run_id)
    findings: list[dict[str, Any]] = []

    # 1. Duplicate bank rows (same account, date, amount, normalized description)
    bank_groups: dict[tuple[str, str, Decimal, str], list[str]] = defaultdict(list)
    for r in ds.bank_rows:
        if r["state"] == "excluded":
            continue
        key = (
            str(r["account_id"]),
            str(r["accounting_date"]),
            Decimal(str(r["amount"])),
            _norm(str(r["description"])),
        )
        bank_groups[key].append(str(r["id"]))
    for (_acc_id, dt_str, amt, desc_norm), rids in bank_groups.items():
        if len(rids) > 1:
            findings.append(
                _make_finding(
                    severity="Medium",
                    category="anomaly",
                    rule_violated="RULE-ANOM-001:DUPLICATE_BANK_TRANSACTIONS",
                    affected_records=rids,
                    expected_vs_actual={
                        "expected_occurrences": "1",
                        "actual_occurrences": str(len(rids)),
                        "amount": str(amt),
                    },
                    evidence=[
                        f"{len(rids)} bank rows on {dt_str} for {amt} {ds.currency} "
                        f"('{desc_norm[:80]}') share identical account, date, amount, and memo."
                    ],
                    recommendation=(
                        "Confirm whether these are duplicate statement lines or legitimate "
                        "repeated charges; exclude duplicates if needed."
                    ),
                )
            )

    # 2. Duplicate trade documents (same party, kind, date, total)
    doc_groups: dict[tuple[str, str, str, Decimal], list[str]] = defaultdict(list)
    for d in ds.trade_documents:
        doc_key = (
            str(d["party_id"]),
            str(d["kind"]),
            str(d["accounting_date"]),
            Decimal(str(d["total"])),
        )
        doc_groups[doc_key].append(f"{d['number']} ({d['id']})")
    for (_pid, kind, dt_str, tot), dids in doc_groups.items():
        if len(dids) > 1:
            findings.append(
                _make_finding(
                    severity="High",
                    category="anomaly",
                    rule_violated="RULE-ANOM-001:DUPLICATE_TRADE_DOCUMENTS",
                    affected_records=dids,
                    expected_vs_actual={
                        "expected_occurrences": "1",
                        "actual_occurrences": str(len(dids)),
                        "total": str(tot),
                    },
                    evidence=[
                        f"{len(dids)} {kind}(s) issued on {dt_str} for the same contact and "
                        f"exact total {tot} {ds.currency}: {', '.join(dids)}."
                    ],
                    recommendation="Verify with source invoices/receipts and credit any duplicate.",
                )
            )

    # 3. Duplicate manual journals (same date, normalized memo, identical line amounts)
    manual_groups: dict[tuple[str, str, tuple[tuple[str, Decimal, Decimal], ...]], list[str]] = (
        defaultdict(list)
    )
    for j in ds.journals:
        if (
            j["status"] != "posted"
            or j.get("source_kind") != "manual_journal"
            or j.get("reversal_of")
        ):
            continue
        jid = str(j["id"])
        sig = tuple(
            sorted(
                (
                    str(ln["account_id"]),
                    Decimal(str(ln["debit"])),
                    Decimal(str(ln["credit"])),
                )
                for ln in ds.journal_lines_by_journal.get(jid, [])
            )
        )
        if sig:
            manual_groups[(str(j["accounting_date"]), _norm(str(j["memo"])), sig)].append(jid)
    for (dt_str, memo_norm, _sig), jids in manual_groups.items():
        if len(jids) > 1:
            findings.append(
                _make_finding(
                    severity="Medium",
                    category="anomaly",
                    rule_violated="RULE-ANOM-001:DUPLICATE_MANUAL_JOURNALS",
                    affected_records=jids,
                    expected_vs_actual={
                        "expected_occurrences": "1",
                        "actual_occurrences": str(len(jids)),
                    },
                    evidence=[
                        f"{len(jids)} manual journals on {dt_str} ('{memo_norm[:80]}') have "
                        "identical account lines and amounts."
                    ],
                    recommendation="Reverse duplicate manual postings if entered twice.",
                )
            )

    # 4. Unallocated / orphaned payment remainders & back-dated journals
    for pay in ds.payments:
        if pay["kind"] not in {"receipt", "payment"}:
            continue
        alloc_sum = sum(
            (Decimal(str(a.get("amount", "0"))) for a in (pay.get("allocations") or [])),
            Decimal("0"),
        )
        pay_tot = Decimal(str(pay["total"]))
        if alloc_sum < pay_tot:
            findings.append(
                _make_finding(
                    severity="Medium",
                    category="anomaly",
                    rule_violated="RULE-ANOM-001:UNALLOCATED_PAYMENT_REMAINDER",
                    affected_records=[str(pay["id"])],
                    expected_vs_actual={
                        "payment_total": str(pay_tot),
                        "allocated_total": str(alloc_sum),
                        "unallocated_remainder": str(pay_tot - alloc_sum),
                    },
                    evidence=[
                        f"Payment {pay['id']} on {pay['accounting_date']} has "
                        f"{pay_tot - alloc_sum} {ds.currency} unallocated to any invoice or bill."
                    ],
                    recommendation="Allocate the remaining settlement or reclassify as an advance.",
                )
            )

    backdated_ids: list[str] = []
    max_lag_days = 0
    for j in ds.journals:
        if j["status"] != "posted" or not j.get("created_at"):
            continue
        acc_dt = date.fromisoformat(str(j["accounting_date"]))
        cre_dt = date.fromisoformat(str(j["created_at"]))
        lag = (cre_dt - acc_dt).days
        if lag > 30:
            backdated_ids.append(str(j["id"]))
            max_lag_days = max(max_lag_days, lag)
    if backdated_ids:
        findings.append(
            _make_finding(
                severity="Medium" if max_lag_days <= 90 else "High",
                category="anomaly",
                rule_violated="RULE-ANOM-001:BACKDATED_TRANSACTIONS",
                affected_records=backdated_ids[:20],
                expected_vs_actual={
                    "max_recommended_lag_days": "30",
                    "observed_max_lag_days": str(max_lag_days),
                    "backdated_count": str(len(backdated_ids)),
                },
                evidence=[
                    f"{len(backdated_ids)} posted journal(s) were entered more than 30 days "
                    f"after their accounting date (maximum lag: {max_lag_days} days)."
                ],
                recommendation=(
                    "Review authorization and cutover impact for late back-dated postings."
                ),
            )
        )

    # 5. Statistical magnitude outliers per account (>= 5 postings, > 3x median & mean + 2.5*std)
    magnitudes_by_acc: dict[str, list[tuple[Decimal, str]]] = defaultdict(list)
    for j in ds.journals:
        if j["status"] != "posted":
            continue
        jid = str(j["id"])
        for ln in ds.journal_lines_by_journal.get(jid, []):
            mag = abs(Decimal(str(ln["debit"])) - Decimal(str(ln["credit"])))
            if mag > 0:
                magnitudes_by_acc[str(ln["account_id"])].append((mag, jid))

    for aid, entries in magnitudes_by_acc.items():
        if len(entries) < 5:
            continue
        sorted_mags = sorted(m for m, _ in entries)
        n = len(sorted_mags)
        median = (
            sorted_mags[n // 2]
            if n % 2 == 1
            else (sorted_mags[n // 2 - 1] + sorted_mags[n // 2]) / Decimal("2")
        )
        if median <= 0:
            continue
        mean = sum(sorted_mags, Decimal("0")) / Decimal(n)
        variance = sum(((m - mean) ** 2 for m in sorted_mags), Decimal("0")) / Decimal(n)
        std = variance.sqrt()
        threshold = max(median * Decimal("5"), mean + std * Decimal("2.5"))
        for mag, jid in entries:
            if mag > threshold and mag >= Decimal("1000"):
                acc = ds.accounts.get(aid, {})
                q_med = median.quantize(Decimal("0.01"))
                q_thr = threshold.quantize(Decimal("0.01"))
                findings.append(
                    _make_finding(
                        severity="Medium",
                        category="anomaly",
                        rule_violated="RULE-ANOM-001:UNUSUALLY_LARGE_OUTLIER",
                        affected_records=[jid, aid],
                        expected_vs_actual={
                            "account_median_amount": str(q_med),
                            "outlier_threshold": str(q_thr),
                            "observed_amount": str(mag),
                        },
                        evidence=[
                            f"Journal {jid} posted {mag} {ds.currency} to account "
                            f"{acc.get('code', aid)} ({acc.get('name', 'Account')}), exceeding "
                            f"the account median ({q_med}) and outlier threshold ({q_thr})."
                        ],
                        recommendation=(
                            "Confirm supporting authorization and contract/invoice documentation "
                            "for this unusually large entry."
                        ),
                    )
                )

    return {
        "tool": "detect_ledger_and_bank_anomalies",
        "findings": findings,
    }


AUDIT_TOOLS: tuple[BaseTool, ...] = (
    retrieve_audit_context_tool,
    evaluate_banking_rules_tool,
    evaluate_accounting_rules_tool,
    evaluate_payment_and_close_rules_tool,
    reconcile_balances_and_continuity_tool,
    detect_ledger_and_bank_anomalies_tool,
)
