"""Deterministic allocation with an exact, stable remainder distribution."""

from decimal import ROUND_FLOOR, Decimal, localcontext

from ledgerpilot.shared.errors import DomainError


def allocate(amount: Decimal, weights: list[Decimal], precision: int) -> list[Decimal]:
    if (
        not amount.is_finite()
        or amount < 0
        or not 0 <= precision <= 6
        or not weights
        or any(not w.is_finite() or w < 0 for w in weights)
    ):
        raise DomainError("ERR_AMOUNT", "Invalid allocation.")
    with localcontext() as context:
        context.prec = 60
        quantum = Decimal(1).scaleb(-precision)
        if amount != amount.quantize(quantum) or sum(weights) <= 0:
            raise DomainError("ERR_AMOUNT", "Allocation requires an exact approved amount.")
        units = int(amount / quantum)
        total = sum(weights)
        exact = [Decimal(units) * weight / total for weight in weights]
        whole = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
        remainder = units - sum(whole)
        order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - whole[i]), i))
        for index in order[:remainder]:
            whole[index] += 1
        return [Decimal(value) * quantum for value in whole]
