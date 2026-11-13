"""Historical-error intervals with a chronological validation gate and abstention."""

from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ledgerpilot.identity.security import now
from ledgerpilot.reports.service import balance_snapshots
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.workspace.models import ReportRun


class CashInterval(Schema):
    report_id: UUID
    week: int
    method: str = "historical_absolute_error_v1"
    target_coverage: str = "0.8"
    status: Literal["insufficient_history", "validation_failed", "available"]
    calibration_count: int
    validation_count: int
    validation_coverage: str | None = None
    lower_cash: str | None = None
    upper_cash: str | None = None
    source_report_ids: list[UUID]
    limitations: str = (
        "Historical validation is not a guarantee of future coverage. "
        "Actuals reflect posted balances at evaluation time, including later corrections. "
        "Only matching cash accounts and non-overlapping forecast horizons are sampled."
    )


def calibrated_radius(errors: list[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    """Use 20 past calibration samples, then 10 untouched temporal validation samples."""
    if len(errors) != 30 or any(not error.is_finite() for error in errors):
        return None, None
    calibration, validation = errors[:20], errors[20:]
    rank = int(
        (Decimal(len(calibration) + 1) * Decimal("0.8")).to_integral_value(rounding=ROUND_CEILING)
    )
    radius = sorted(abs(error) for error in calibration)[rank - 1]
    coverage = Decimal(sum(abs(error) <= radius for error in validation)) / len(validation)
    return (radius if coverage >= Decimal("0.8") else None), coverage


def interval(db: Session, bid: UUID, report_id: UUID, week: int) -> CashInterval:
    target = db.scalar(
        select(ReportRun).where(
            ReportRun.business_id == bid,
            ReportRun.id == report_id,
            ReportRun.kind == "deterministic_cash_plan",
        )
    )
    if target is None:
        raise DomainError("ERR_NOT_FOUND", "Saved cash forecast not found.", 404)
    data = target.result
    accounts = sorted(data.get("cash_account_ids", []))
    if data.get("scenario") or not accounts:
        raise DomainError(
            "ERR_FORECAST_EVALUATION", "Choose a baseline with saved cash accounts.", 422
        )
    timezone = ZoneInfo(data["timezone"])
    as_of = date.fromisoformat(data["as_of"])
    if target.created_at.astimezone(timezone).date() != as_of:
        raise DomainError(
            "ERR_FORECAST_EVALUATION", "The baseline must be saved on its forecast date.", 422
        )
    if as_of > now().astimezone(timezone).date():
        raise DomainError(
            "ERR_FORECAST_EVALUATION", "A future-dated baseline cannot be calibrated.", 422
        )
    candidates = list(
        db.scalars(
            select(ReportRun)
            .where(
                ReportRun.business_id == bid,
                ReportRun.kind == "deterministic_cash_plan",
                ReportRun.created_at < target.created_at,
                ReportRun.result["currency"].astext == data["currency"],
                ReportRun.result["timezone"].astext == data["timezone"],
            )
            .order_by(ReportRun.created_at.desc(), ReportRun.id)
            .limit(501)
        )
    )
    if len(candidates) > 500:
        raise DomainError(
            "ERR_QUOTA", "Calibration history exceeds the local 500-report limit.", 413
        )
    # Keep the newest baseline for each eligible window, then separate horizons.
    samples: list[tuple[ReportRun, date, Decimal]] = []
    next_start = as_of
    for candidate in candidates:
        value = candidate.result
        if value.get("scenario") or sorted(value.get("cash_account_ids", [])) != accounts:
            continue
        start = date.fromisoformat(value["as_of"])
        if candidate.created_at.astimezone(timezone).date() != start:
            continue
        if len(value.get("weeks", [])) < week:
            continue
        row = value["weeks"][week - 1]
        end = date.fromisoformat(row["end_date"])
        if end >= next_start:
            continue
        samples.append((candidate, end, Decimal(row["closing_cash"])))
        next_start = start
        if len(samples) == 30:
            break
    samples.reverse()
    base = dict(
        report_id=report_id,
        week=week,
        calibration_count=min(len(samples), 20),
        validation_count=max(len(samples) - 20, 0),
        source_report_ids=[sample[0].id for sample in samples],
    )
    if len(samples) < 30:
        return CashInterval(**base, status="insufficient_history")
    actuals: dict[date, dict[UUID, Decimal]] = {}
    # Preserve the shared 14-checkpoint guard: exactly three bounded reads for 30 samples.
    for offset in range(0, len(samples), 14):
        actuals.update(
            balance_snapshots(
                db,
                bid,
                [sample[1] for sample in samples[offset : offset + 14]],
                [UUID(account) for account in accounts],
            )
        )
    errors = [sum(actuals[end].values(), Decimal(0)) - forecast for _, end, forecast in samples]
    radius, coverage = calibrated_radius(errors)
    if radius is None:
        return CashInterval(**base, status="validation_failed", validation_coverage=str(coverage))
    center = Decimal(data["weeks"][week - 1]["closing_cash"])
    return CashInterval(
        **base,
        status="available",
        validation_coverage=str(coverage),
        lower_cash=str(center - radius),
        upper_cash=str(center + radius),
    )
