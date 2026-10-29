"""Reviewed opening cash-basis adjustments; never infer pre-cutover tax or profit."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.money import allocate
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import get_business
from ledgerpilot.trade.models import Payment, TradeDocument
from ledgerpilot.workspace.models import OpeningCashPolicy, OpeningCorrection, OpeningItem


def adjustments(
    db: Session,
    bid: UUID,
    as_of: date,
    docs: list[TradeDocument],
    realized: dict[UUID, Decimal],
    payments: list[Payment],
) -> tuple[set[UUID], dict[UUID, Decimal]]:
    roots = {
        r.id: r
        for r in db.scalars(
            select(OpeningItem).where(
                OpeningItem.business_id == bid,
                OpeningItem.document_id.is_not(None),
                OpeningItem.cutover_date <= as_of,
            )
        )
    }
    policies = {
        p.opening_id: p
        for p in db.scalars(
            select(OpeningCashPolicy).where(
                OpeningCashPolicy.business_id == bid, OpeningCashPolicy.opening_id.in_(roots)
            )
        )
    }
    if roots.keys() - policies.keys():
        raise DomainError(
            "ERR_CASH_POLICY", "Opening trade balances require evidence-backed cash treatment.", 409
        )
    by_document = {r.document_id: r for r in roots.values()}
    for correction in db.scalars(
        select(OpeningCorrection).where(
            OpeningCorrection.business_id == bid,
            OpeningCorrection.opening_id.in_(roots),
            OpeningCorrection.delta > 0,
            OpeningCorrection.document_id.is_not(None),
            OpeningCorrection.accounting_date <= as_of,
        )
    ):
        by_document[correction.document_id] = roots[correction.opening_id]
    skip = {d.id for d in docs if d.id in by_document or d.original_id in by_document}
    result: dict[UUID, Decimal] = defaultdict(Decimal)
    precision = get_business(db, bid).currency_precision
    db.info.setdefault("opening_cash_policy_ids", set()).update(
        str(p.id) for p in policies.values()
    )
    for doc in docs:
        root = by_document.get(doc.id)
        if root is None:
            continue
        policy = policies[root.id]
        credits = sum((d.total for d in docs if d.original_id == doc.id), Decimal(0))
        net = doc.total - credits
        refunded = sum(
            (
                Decimal(a["amount"])
                for p in payments
                if p.kind in {"customer_refund", "vendor_refund"}
                for a in p.allocations
                if a["document_id"] == str(doc.id)
            ),
            Decimal(0),
        )
        cash = realized.get(doc.id, Decimal(0)) - refunded
        remaining = net - cash
        if cash < 0:
            raise DomainError(
                "ERR_CASH_POLICY", "Opening cash recognition does not reconcile.", 409
            )
        sign = Decimal(1) if root.kind == "customer" else Decimal(-1)
        result[doc.control_account_id] -= remaining * sign
        result[root.equity_account_id] += (
            net if policy.treatment == "on_settlement" else remaining
        ) * sign
        if policy.treatment == "on_settlement" and cash:
            pieces = allocate(cash, [Decimal(c["weight"]) for c in policy.components], precision)
            for component, value in zip(policy.components, pieces, strict=True):
                result[UUID(component["account_id"])] -= value * sign
    return skip, result
