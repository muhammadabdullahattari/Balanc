"""Financial, aging, control and deterministic forecast endpoints."""

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.reports import service
from ledgerpilot.shared import contracts
from ledgerpilot.tenancy.service import authorize
from ledgerpilot.workspace.service import save_report

router = APIRouter(prefix="/businesses/{business_id}", tags=["reports"])


@router.get(
    "/reports/tax-support",
    response_model=contracts.responses["reports.tax_support"],
    response_model_exclude_unset=True,
)
def tax_support(
    business_id: UUID,
    start: date,
    end: date,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(db, identity, business_id, service.tax_support(db, business_id, start, end))


@router.get(
    "/dashboard",
    response_model=contracts.responses["reports.dashboard"],
    response_model_exclude_unset=True,
)
def dashboard(
    business_id: UUID,
    start: date,
    end: date,
    identity: Identity,
    db: DB,
    bank_ids: Annotated[list[UUID], Query(max_length=20)],
    basis: Literal["cash", "accrual"] = "accrual",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(
        db, identity, business_id, service.dashboard(db, business_id, start, end, basis, bank_ids)
    )


@router.get(
    "/reports/profit-loss",
    response_model=contracts.responses["reports.profit_loss"],
    response_model_exclude_unset=True,
)
def profit_loss(
    business_id: UUID,
    start: date,
    end: date,
    identity: Identity,
    db: DB,
    basis: Literal["cash", "accrual"] = "accrual",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(
        db,
        identity,
        business_id,
        service.financial(db, business_id, start, end, basis, "profit-loss"),
    )


@router.get(
    "/reports/balance-sheet",
    response_model=contracts.responses["reports.balance_sheet"],
    response_model_exclude_unset=True,
)
def balance_sheet(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    basis: Literal["cash", "accrual"] = "accrual",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(
        db,
        identity,
        business_id,
        service.financial(db, business_id, date(1900, 1, 1), as_of, basis, "balance-sheet"),
    )


@router.get(
    "/reports/aging",
    response_model=contracts.responses["reports.aging"],
    response_model_exclude_unset=True,
)
def aging(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    kind: Literal["invoice", "bill"] = "invoice",
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(db, identity, business_id, service.aging(db, business_id, as_of, kind))


@router.get(
    "/reports/controls",
    response_model=contracts.responses["reports.controls"],
    response_model_exclude_unset=True,
)
def controls(business_id: UUID, as_of: date, identity: Identity, db: DB) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(db, identity, business_id, service.controls(db, business_id, as_of))


@router.get(
    "/reports/forecast",
    response_model=contracts.responses["reports.forecast"],
    response_model_exclude_unset=True,
)
def forecast(
    business_id: UUID,
    as_of: date,
    identity: Identity,
    db: DB,
    bank_ids: Annotated[list[UUID], Query(min_length=1, max_length=20)],
) -> dict[str, Any]:
    authorize(db, identity, business_id)
    return save_report(
        db, identity, business_id, service.forecast(db, business_id, as_of, bank_ids)
    )
