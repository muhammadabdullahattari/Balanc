"""Explicit reviewed tax remittance, distinct from licenses and operating costs."""

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import field_validator

from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared import operations as ops
from ledgerpilot.trade.schemas import exact_amount


class Remittance(ops.Command):
    accounting_date: date
    bank_account_id: UUID
    tax_account_id: UUID
    amount: str
    _money = field_validator("amount")(exact_amount)


def remit(bid: UUID, data: Remittance, who: Identity, db: DB) -> dict[str, Any]:
    business, old = ops.begin(db, who, bid, data, "tax.remitted")
    if old is not None:
        return old
    ops.account(db, bid, data.bank_account_id, {"asset"}, plain=True)
    ops.account(db, bid, data.tax_account_id, {"liability"}, "tax")
    amount = ops.money(data.amount, business)
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.accounting_date,
        "Reviewed tax remittance",
        [
            ops.line(data.tax_account_id, amount, debit=True),
            ops.line(data.bank_account_id, amount, debit=False),
        ],
    )
    return ops.finish(
        db,
        who,
        bid,
        data,
        "tax.remitted",
        {
            "id": str(db.info["operation_id"]),
            "journal_id": str(jid),
            "amount": str(amount),
            "tax_account_id": str(data.tax_account_id),
            "accounting_date": data.accounting_date.isoformat(),
        },
        jid,
    )
