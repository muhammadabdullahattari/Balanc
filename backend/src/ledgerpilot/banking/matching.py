"""Deterministic 1..5 invoice bundle, fee-tolerance, and FX payment matching engine."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Any


def _q2(val: Decimal | str | int | float) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class MatchCandidateDoc:
    document_id: str
    number: str
    kind: str
    outstanding: Decimal
    currency: str = "USD"
    due_date: str = ""
    party_name: str = ""


@dataclass(frozen=True)
class MatchProposal:
    match_kind: str
    components: list[dict[str, Any]]
    allocated_total: Decimal
    fee_amount: Decimal
    fx_gain_loss: Decimal
    residual: Decimal
    confidence: Decimal
    explanation: str
    proposed_allocation: dict[str, Any]


def find_best_document_match(
    statement_amount: Decimal | str,
    open_docs: list[MatchCandidateDoc],
    *,
    statement_currency: str = "USD",
    fx_rates: dict[str, Decimal] | None = None,
    max_bundle_size: int = 5,
    max_candidates: int = 40,
    fee_min_ratio: Decimal = Decimal("0.005"),
    fee_max_ratio: Decimal = Decimal("0.055"),
    fx_tolerance_ratio: Decimal = Decimal("0.025"),
    time_budget_seconds: float = 0.20,
) -> MatchProposal | None:
    """Match a bank movement against 1..5 open invoices/bills with fee and FX awareness."""
    stmt_amt = abs(_q2(statement_amount))
    if stmt_amt <= Decimal("0.00") or not open_docs:
        return None

    stmt_ccy = statement_currency.upper().strip() or "USD"
    rates = {k.upper(): Decimal(str(v)) for k, v in (fx_rates or {}).items()}
    bounded_docs = sorted(
        [d for d in open_docs if _q2(d.outstanding) > Decimal("0.00")],
        key=lambda d: (d.due_date or "9999-12-31", d.number, d.document_id),
    )[:max_candidates]

    same_ccy_docs = [d for d in bounded_docs if (d.currency or stmt_ccy).upper() == stmt_ccy]

    # 1. Exact single-document match in same currency
    for doc in same_ccy_docs:
        out_amt = _q2(doc.outstanding)
        if out_amt == stmt_amt:
            comp = {
                "document_id": doc.document_id,
                "number": doc.number,
                "kind": doc.kind,
                "outstanding": f"{out_amt:.2f}",
                "allocated": f"{stmt_amt:.2f}",
                "currency": stmt_ccy,
            }
            return MatchProposal(
                match_kind="exact_match",
                components=[comp],
                allocated_total=stmt_amt,
                fee_amount=Decimal("0.00"),
                fx_gain_loss=Decimal("0.00"),
                residual=Decimal("0.00"),
                confidence=Decimal("0.9850"),
                explanation=(
                    f"Exact 1-to-1 match: bank amount {stmt_ccy} {stmt_amt:.2f} settles "
                    f"{doc.kind} {doc.number} ({stmt_ccy} {out_amt:.2f}) in full."
                ),
                proposed_allocation={
                    "action": "allocate_single_document",
                    "document_ids": [doc.document_id],
                    "allocated_amount": f"{stmt_amt:.2f}",
                },
            )

    # 2. Exact multi-document bundle (k = 2..max_bundle_size, e.g. 1,240 covering 3 invoices)
    deadline = time.monotonic() + max(0.02, time_budget_seconds)
    by_kind: dict[str, list[MatchCandidateDoc]] = {}
    for d in same_ccy_docs:
        by_kind.setdefault(d.kind, []).append(d)

    upper_k = min(max_bundle_size, 5)
    for _kind, kind_docs in by_kind.items():
        eligible = [d for d in kind_docs if _q2(d.outstanding) <= stmt_amt]
        for k in range(2, min(upper_k, len(eligible)) + 1):
            for combo in combinations(eligible, k):
                if time.monotonic() > deadline:
                    break
                combo_sum = sum((_q2(d.outstanding) for d in combo), Decimal("0.00"))
                if combo_sum == stmt_amt:
                    comps = [
                        {
                            "document_id": d.document_id,
                            "number": d.number,
                            "kind": d.kind,
                            "outstanding": f"{_q2(d.outstanding):.2f}",
                            "allocated": f"{_q2(d.outstanding):.2f}",
                            "currency": stmt_ccy,
                        }
                        for d in combo
                    ]
                    nums = ", ".join(f"{d.number} ({_q2(d.outstanding):.2f})" for d in combo)
                    return MatchProposal(
                        match_kind="bundled_payment",
                        components=comps,
                        allocated_total=stmt_amt,
                        fee_amount=Decimal("0.00"),
                        fx_gain_loss=Decimal("0.00"),
                        residual=Decimal("0.00"),
                        confidence=Decimal("0.9550"),
                        explanation=(
                            f"Bundled settlement: {stmt_ccy} {stmt_amt:.2f} exactly covers "
                            f"{len(combo)} open documents ({nums})."
                        ),
                        proposed_allocation={
                            "action": "allocate_bundled_documents",
                            "document_ids": [d.document_id for d in combo],
                            "allocated_amount": f"{stmt_amt:.2f}",
                            "bundle_count": len(combo),
                        },
                    )

    # 3. Single or bundled payment with processor fee deducted (0.5%..5.5%)
    for _kind, kind_docs in by_kind.items():
        for k in range(1, min(3, len(kind_docs)) + 1):
            for combo in combinations(kind_docs, k):
                if time.monotonic() > deadline:
                    break
                gross = sum((_q2(d.outstanding) for d in combo), Decimal("0.00"))
                if gross <= stmt_amt:
                    continue
                fee_diff = _q2(gross - stmt_amt)
                ratio = fee_diff / gross
                if fee_min_ratio <= ratio <= fee_max_ratio:
                    comps = [
                        {
                            "document_id": d.document_id,
                            "number": d.number,
                            "kind": d.kind,
                            "outstanding": f"{_q2(d.outstanding):.2f}",
                            "implied_fee": f"{fee_diff:.2f}" if idx == 0 else "0.00",
                            "currency": stmt_ccy,
                        }
                        for idx, d in enumerate(combo)
                    ]
                    nums = ", ".join(d.number for d in combo)
                    return MatchProposal(
                        match_kind="fee_deducted",
                        components=comps,
                        allocated_total=gross,
                        fee_amount=fee_diff,
                        fx_gain_loss=Decimal("0.00"),
                        residual=Decimal("0.00"),
                        confidence=Decimal("0.9200"),
                        explanation=(
                            f"Gross-to-net fee match: {stmt_ccy} {stmt_amt:.2f} received settles "
                            f"{nums} ({stmt_ccy} {gross:.2f}) less {stmt_ccy} {fee_diff:.2f} "
                            f"processing fee ({(ratio * 100):.2f}%)."
                        ),
                        proposed_allocation={
                            "action": "split_gross_and_processing_fee",
                            "document_ids": [d.document_id for d in combo],
                            "net_cash": f"{stmt_amt:.2f}",
                            "fee_expense": f"{fee_diff:.2f}",
                            "gross_settled": f"{gross:.2f}",
                        },
                    )

    # 4. Cross-currency FX settlement match (using fx_rates[doc_ccy] -> statement_currency)
    fx_docs = [
        d
        for d in bounded_docs
        if (d.currency or stmt_ccy).upper() != stmt_ccy and (d.currency or "").upper() in rates
    ]
    for doc in fx_docs:
        doc_ccy = doc.currency.upper()
        rate = rates[doc_ccy]
        expected_base = _q2(_q2(doc.outstanding) * rate)
        if expected_base <= Decimal("0.00"):
            continue
        fx_diff = _q2(stmt_amt - expected_base)
        rel_diff = abs(fx_diff) / expected_base
        if rel_diff <= fx_tolerance_ratio:
            comp = {
                "document_id": doc.document_id,
                "number": doc.number,
                "kind": doc.kind,
                "foreign_outstanding": f"{_q2(doc.outstanding):.2f}",
                "foreign_currency": doc_ccy,
                "quoted_rate": str(rate),
                "expected_base_amount": f"{expected_base:.2f}",
                "realized_fx_variance": f"{fx_diff:.2f}",
            }
            gain_or_loss = "gain" if fx_diff >= Decimal("0.00") else "loss"
            return MatchProposal(
                match_kind="fx_settlement",
                components=[comp],
                allocated_total=expected_base,
                fee_amount=Decimal("0.00"),
                fx_gain_loss=fx_diff,
                residual=Decimal("0.00"),
                confidence=Decimal("0.9100"),
                explanation=(
                    f"Cross-currency settlement: {stmt_ccy} {stmt_amt:.2f} matches {doc.number} "
                    f"({doc_ccy} {_q2(doc.outstanding):.2f} @ {rate} = "
                    f"{stmt_ccy} {expected_base:.2f}) "
                    f"with {stmt_ccy} {abs(fx_diff):.2f} realized FX {gain_or_loss}."
                ),
                proposed_allocation={
                    "action": "allocate_with_realized_fx",
                    "document_ids": [doc.document_id],
                    "foreign_currency": doc_ccy,
                    "quoted_rate": str(rate),
                    "settled_base_amount": f"{expected_base:.2f}",
                    "realized_fx_gain_loss": f"{fx_diff:.2f}",
                },
            )

    # 5. Partial settlement of an open same-currency document
    partials = [d for d in same_ccy_docs if _q2(d.outstanding) > stmt_amt]
    if partials:
        doc = partials[0]
        rem = _q2(_q2(doc.outstanding) - stmt_amt)
        comp = {
            "document_id": doc.document_id,
            "number": doc.number,
            "kind": doc.kind,
            "outstanding": f"{_q2(doc.outstanding):.2f}",
            "remaining_after_partial": f"{rem:.2f}",
            "currency": stmt_ccy,
        }
        return MatchProposal(
            match_kind="partial_settlement",
            components=[comp],
            allocated_total=stmt_amt,
            fee_amount=Decimal("0.00"),
            fx_gain_loss=Decimal("0.00"),
            residual=rem,
            confidence=Decimal("0.8600"),
            explanation=(
                f"Partial payment: {stmt_ccy} {stmt_amt:.2f} partially settles {doc.number} "
                f"({stmt_ccy} {_q2(doc.outstanding):.2f}), leaving {stmt_ccy} {rem:.2f} open."
            ),
            proposed_allocation={
                "action": "partial_allocation",
                "document_ids": [doc.document_id],
                "allocated_amount": f"{stmt_amt:.2f}",
                "remaining_open": f"{rem:.2f}",
            },
        )

    return None
