"""Compare authoritative subledger balances with their signed GL control balance."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from ledgerpilot.shared.errors import DomainError


@dataclass(frozen=True)
class Reconciliation:
    ledger_balance: Decimal
    subledger_balance: Decimal
    difference: Decimal

    @property
    def balanced(self) -> bool:
        return self.difference == 0


def reconcile_control(
    ledger_balance: Decimal, balances: Sequence[Decimal], *, source_complete: bool
) -> Reconciliation:
    if not source_complete:
        raise DomainError("ERR_SUBLEDGER_UNAVAILABLE", "Complete subledger data is required.", 409)
    if not ledger_balance.is_finite() or any(not value.is_finite() for value in balances):
        raise DomainError("ERR_AMOUNT", "Invalid reconciliation amount.")
    with localcontext() as context:
        context.prec = 60
        total = sum(balances, Decimal(0))
        return Reconciliation(ledger_balance, total, ledger_balance - total)
