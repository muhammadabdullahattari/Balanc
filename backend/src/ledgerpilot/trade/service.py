"""Source workflows post accrual-capable journals regardless of report basis."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.identity.security import canonical_email
from ledgerpilot.shared import operations as ops
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.models import Business
from ledgerpilot.tenancy.service import authorize, get_business
from ledgerpilot.trade import repository as repo
from ledgerpilot.trade.models import Allocation, Loan, LoanMovement, Party, Payment, TradeDocument
from ledgerpilot.trade.schemas import (
    DocumentCreate,
    LoanCreate,
    LoanMovementCreate,
    PartyCreate,
    PartyUpdate,
    PaymentCreate,
)


def create_party(db: Session, who: Principal, bid: UUID, data: PartyCreate) -> Party:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    item = Party(
        business_id=bid,
        name=data.name,
        email=canonical_email(data.email) if data.email else None,
        roles=data.roles,
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "party.created", bid, item.id)
    return item


def update_party(db: Session, who: Principal, bid: UUID, pid: UUID, data: PartyUpdate) -> Party:
    authorize(db, who, bid, {"owner", "accountant", "bookkeeper"})
    get_business(db, bid, lock=True)
    item = db.scalar(
        select(Party).where(Party.business_id == bid, Party.id == pid).with_for_update()
    )
    if not item:
        raise DomainError("ERR_NOT_FOUND", "Party not found.", 404)
    if item.version != data.expected_version:
        raise DomainError("ERR_VERSION", "Party changed. Reload before retrying.", 409)
    removed = set(item.roles) - set(data.roles)
    if removed:
        from ledgerpilot.workspace.models import Advance

        kinds = set(
            db.scalars(
                select(TradeDocument.kind).where(
                    TradeDocument.business_id == bid, TradeDocument.party_id == pid
                )
            ).all()
        )
        directions = set(
            db.scalars(
                select(Loan.direction).where(Loan.business_id == bid, Loan.party_id == pid)
            ).all()
        )
        deposits = set(
            db.scalars(
                select(Advance.direction).where(Advance.business_id == bid, Advance.party_id == pid)
            ).all()
        )
        historical = set()
        if kinds & {"invoice", "sales_receipt", "customer_credit"} or "customer" in deposits:
            historical.add("customer")
        if kinds & {"bill", "vendor_credit"} or "vendor" in deposits:
            historical.add("supplier")
        if "payable" in directions:
            historical.add("lender")
        if "receivable" in directions:
            historical.add("borrower")
        if removed & historical:
            raise DomainError(
                "ERR_PARTY_HISTORY",
                "Keep roles with financial history; deactivate the party to stop new transactions.",
                409,
            )
    item.name, item.roles, item.active = data.name, list(data.roles), data.active
    item.email = canonical_email(data.email) if data.email else None
    item.version += 1
    record(db, who.user.id, "party.updated", bid, pid)
    return item


def document_impact(
    db: Session,
    business: Business,
    data: DocumentCreate,
) -> tuple[list[LineCreate], Decimal, Decimal]:
    bid = business.id
    sale = data.kind in {"invoice", "sales_receipt", "customer_credit"}
    credit = data.kind in {"customer_credit", "vendor_credit"}
    repo.party(db, bid, data.party_id, "customer" if sale else "supplier")
    control = "ar" if sale else "ap"
    ops.account(db, bid, data.control_account_id, {"asset"} if sale else {"liability"}, control)
    if data.original_id:
        original = repo.document(db, bid, data.original_id)
        if (
            original.kind != ("invoice" if sale else "bill")
            or original.party_id != data.party_id
            or original.control_account_id != data.control_account_id
            or data.accounting_date < original.accounting_date
        ):
            raise DomainError(
                "ERR_CREDIT", "Credit must reference the same party and control account."
            )
        # Reverse the original categories and tax policy proportionally, not arbitrary costs.
        original_accounts = {
            (r["account_id"], r["tax_account_id"], r["tax_code"]) for r in original.lines
        }
        if any(
            (str(r.account_id), str(r.tax_account_id) if r.tax_account_id else None, r.tax_code)
            not in original_accounts
            for r in data.lines
        ):
            raise DomainError("ERR_CREDIT", "Credit categories must match the original source.")
    lines = []
    total, tax_total = Decimal(0), Decimal(0)
    for source in data.lines:
        amount = ops.money(source.amount, business)
        tax = ops.money(source.tax_amount, business, positive=False)
        ops.account(
            db, bid, source.account_id, {"income"} if sale else {"expense", "asset"}, plain=True
        )
        lines.append(ops.line(source.account_id, amount, debit=(not sale) != credit))
        if tax:
            if source.tax_account_id is None or not source.tax_code:
                raise DomainError("ERR_TAX", "Explicit tax account and policy code are required.")
            ops.account(db, bid, source.tax_account_id, {"liability"} if sale else {"asset"}, "tax")
            lines.append(ops.line(source.tax_account_id, tax, debit=(not sale) != credit))
        total += amount + tax
        tax_total += tax
    if data.original_id:
        previous_credit = db.scalar(
            select(func.coalesce(func.sum(TradeDocument.total), 0)).where(
                TradeDocument.business_id == bid,
                TradeDocument.original_id == data.original_id,
            )
        ) or Decimal(0)
        if previous_credit + total > original.total:
            raise DomainError("ERR_CREDIT", "Credit exceeds the original document total.", 409)
        remaining: dict[tuple[str, str | None, str | None], list[Decimal]] = {}
        for row in original.lines:
            key = (row["account_id"], row["tax_account_id"], row["tax_code"])
            values = remaining.setdefault(key, [Decimal(0), Decimal(0)])
            values[0] += Decimal(row["amount"])
            values[1] += Decimal(row["tax_amount"])
        previous_notes = db.scalars(
            select(TradeDocument).where(
                TradeDocument.business_id == bid,
                TradeDocument.original_id == data.original_id,
            )
        )
        for note in previous_notes:
            for row in note.lines:
                key = (row["account_id"], row["tax_account_id"], row["tax_code"])
                remaining[key][0] -= Decimal(row["amount"])
                remaining[key][1] -= Decimal(row["tax_amount"])
        for source in data.lines:
            key = (
                str(source.account_id),
                str(source.tax_account_id) if source.tax_account_id else None,
                source.tax_code,
            )
            values = remaining[key]
            values[0] -= Decimal(source.amount)
            values[1] -= Decimal(source.tax_amount)
            if min(values) < 0:
                raise DomainError(
                    "ERR_CREDIT", "Credit exceeds an original category or tax component.", 409
                )
    offset = data.control_account_id
    if data.kind == "sales_receipt":
        assert data.bank_account_id
        ops.account(db, bid, data.bank_account_id, {"asset"}, plain=True)
        offset = data.bank_account_id
    lines.append(ops.line(offset, total, debit=sale != credit))
    return lines, total, tax_total


def create_document(
    db: Session,
    who: Principal,
    bid: UUID,
    data: DocumentCreate,
) -> dict[str, object]:
    business, previous = ops.begin(db, who, bid, data, "trade.issued")
    if previous is not None:
        return previous
    lines, total, tax_total = document_impact(db, business, data)
    jid = ops.publish(
        db, who, business, data, data.accounting_date, f"{data.kind}: {data.number}", lines
    )
    item = TradeDocument(
        business_id=bid,
        kind=data.kind,
        number=data.number,
        party_id=data.party_id,
        accounting_date=data.accounting_date,
        due_date=data.due_date,
        service_start=data.service_start,
        service_end=data.service_end,
        control_account_id=data.control_account_id,
        journal_id=jid,
        total=total,
        tax=tax_total,
        lines=[r.model_dump(mode="json") for r in data.lines],
        original_id=data.original_id,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        "trade.issued",
        {"id": str(item.id), "journal_id": str(jid), "total": str(total)},
        jid,
    )


def create_payment(
    db: Session,
    who: Principal,
    bid: UUID,
    data: PaymentCreate,
) -> dict[str, object]:
    business, old = ops.begin(db, who, bid, data, "payment.posted")
    if old is not None:
        return old
    sale = data.kind in {"receipt", "customer_refund"}
    refund = data.kind in {"customer_refund", "vendor_refund"}
    repo.party(db, bid, data.party_id, "customer" if sale else "supplier")
    ops.account(db, bid, data.bank_account_id, {"asset"}, plain=True)
    ops.account(
        db,
        bid,
        data.control_account_id,
        {"asset"} if sale else {"liability"},
        "ar" if sale else "ap",
    )
    amount = ops.money(data.amount, business)
    if len({a.document_id for a in data.allocations}) != len(data.allocations):
        raise DomainError("ERR_ALLOCATION", "A document may occur only once per payment.")
    balances = repo.due(db, bid, data.accounting_date)
    closing_balances = repo.due(db, bid, date.max)
    if refund:
        if not data.allocations or not data.original_payment_id:
            raise DomainError(
                "ERR_REFUND",
                "Refunds require an original payment and explicit credited-document allocations.",
            )
        original = db.scalar(
            select(Payment).where(
                Payment.business_id == bid,
                Payment.id == data.original_payment_id,
            )
        )
        if (
            not original
            or original.kind != ("receipt" if sale else "payment")
            or original.party_id != data.party_id
            or original.control_account_id != data.control_account_id
            or original.accounting_date > data.accounting_date
        ):
            raise DomainError("ERR_REFUND", "Original payment is incompatible.")
        available = {UUID(r["document_id"]): Decimal(r["amount"]) for r in original.allocations}
        for earlier in db.scalars(
            select(Payment).where(
                Payment.business_id == bid,
                Payment.original_payment_id == original.id,
            )
        ):
            for row in earlier.allocations:
                available[UUID(row["document_id"])] -= Decimal(row["amount"])
        allocated = Decimal(0)
        credit_refunds: dict[UUID, Decimal] = dict(
            db.execute(
                text("""
            SELECT (a->>'credit_note_id')::uuid,sum((a->>'amount')::numeric)
            FROM app.payments p CROSS JOIN LATERAL jsonb_array_elements(p.allocations) a
            WHERE p.business_id=:b AND p.kind IN ('customer_refund','vendor_refund')
            AND a->>'credit_note_id' IS NOT NULL GROUP BY a->>'credit_note_id'
        """),
                {"b": bid},
            ).all()
        )
        for allocation in data.allocations:
            value = ops.money(allocation.amount, business)
            if allocation.credit_note_id is None:
                raise DomainError(
                    "ERR_REFUND", "Select the credit note funding each refund allocation."
                )
            credit_note = repo.document(db, bid, allocation.credit_note_id)
            if (
                credit_note.original_id != allocation.document_id
                or credit_note.party_id != data.party_id
                or credit_note.kind != ("customer_credit" if sale else "vendor_credit")
                or credit_note.accounting_date > data.accounting_date
            ):
                raise DomainError("ERR_REFUND", "Credit note does not belong to this settlement.")
            credit_refunded = credit_refunds.get(credit_note.id, Decimal(0))
            if credit_refunded + value > credit_note.total:
                raise DomainError("ERR_REFUND", "Credit note is already refunded.", 409)
            if value > min(
                available.get(allocation.document_id, Decimal(0)),
                -balances.get(allocation.document_id, Decimal(0)),
                -closing_balances.get(allocation.document_id, Decimal(0)),
            ):
                raise DomainError("ERR_REFUND", "Refund exceeds credited, paid funds.", 409)
            allocated += value
        if allocated != amount:
            raise DomainError("ERR_REFUND", "Refund allocations must equal the cash refund.")

    else:
        if data.original_payment_id is not None:
            raise DomainError("ERR_ALLOCATION", "Only refunds reference an original payment.")
        allocated = Decimal(0)
        for allocation in data.allocations:
            if allocation.credit_note_id is not None:
                raise DomainError(
                    "ERR_ALLOCATION", "Credit-note references are only valid on refunds."
                )
            doc = repo.document(db, bid, allocation.document_id)
            value = ops.money(allocation.amount, business)
            if (
                doc.kind != ("invoice" if sale else "bill")
                or doc.party_id != data.party_id
                or doc.control_account_id != data.control_account_id
                or doc.accounting_date > data.accounting_date
                or value > balances.get(doc.id, Decimal(0))
                or value > closing_balances.get(doc.id, Decimal(0))
            ):
                raise DomainError(
                    "ERR_ALLOCATION", "Allocation exceeds the compatible open balance.", 409
                )
            allocated += value
        if allocated != amount:
            raise DomainError(
                "ERR_ALLOCATION", "Allocate the full payment; use advances for excess."
            )
    fee = ops.money(data.fee_amount, business, positive=False)
    if (fee and (refund or data.fee_account_id is None)) or (not fee and data.fee_account_id):
        raise DomainError(
            "ERR_FEE",
            "Settlement fees need a separate expense account and cannot be used on refunds.",
        )
    if fee:
        assert data.fee_account_id
        ops.account(db, bid, data.fee_account_id, {"expense"}, plain=True)
    bank_amount = amount - fee if sale and not refund else amount + fee
    if bank_amount <= 0:
        raise DomainError("ERR_FEE", "The net bank settlement must be positive.")
    payment_lines = [
        ops.line(data.bank_account_id, bank_amount, debit=sale != refund),
        ops.line(data.control_account_id, amount, debit=(not sale) != refund),
    ]
    if fee:
        assert data.fee_account_id
        payment_lines.append(ops.line(data.fee_account_id, fee, debit=True))
    jid = ops.publish(
        db,
        who,
        business,
        data,
        data.accounting_date,
        data.kind,
        payment_lines,
    )
    item = Payment(
        id=uuid4(),
        business_id=bid,
        kind=data.kind,
        party_id=data.party_id,
        accounting_date=data.accounting_date,
        bank_account_id=data.bank_account_id,
        control_account_id=data.control_account_id,
        total=amount,
        fee_amount=fee,
        fee_account_id=data.fee_account_id,
        journal_id=jid,
        allocations=[a.model_dump(mode="json") for a in data.allocations],
        original_payment_id=data.original_payment_id,
    )
    db.add(item)
    db.flush()
    for allocation in [] if refund else data.allocations:
        db.add(
            Allocation(
                business_id=bid,
                payment_id=item.id,
                document_id=allocation.document_id,
                accounting_date=data.accounting_date,
                amount=Decimal(allocation.amount),
            )
        )
    return ops.finish(
        db, who, bid, data, "payment.posted", {"id": str(item.id), "journal_id": str(jid)}, jid
    )


def create_loan(db: Session, who: Principal, bid: UUID, data: LoanCreate) -> Loan:
    authorize(db, who, bid, posting=True)
    business = get_business(db, bid, lock=True)
    repo.party(db, bid, data.party_id, "lender" if data.direction == "payable" else "borrower")
    ops.account(
        db,
        bid,
        data.control_account_id,
        {"liability"} if data.direction == "payable" else {"asset"},
        "loan_" + data.direction,
    )
    for row in data.schedule:
        ops.money(row.principal, business)
        ops.money(row.interest, business, positive=False)
    dates = [row.due_date for row in data.schedule]
    if dates != sorted(set(dates)):
        raise DomainError("ERR_SCHEDULE", "Loan schedule dates must be unique and ordered.")
    item = Loan(
        business_id=bid,
        party_id=data.party_id,
        direction=data.direction,
        control_account_id=data.control_account_id,
        name=data.name,
        schedule=[row.model_dump(mode="json") for row in data.schedule],
    )
    db.add(item)
    db.flush()
    record(db, who.user.id, "loan.created", bid, item.id)
    return item


def loan_movement(
    db: Session,
    who: Principal,
    bid: UUID,
    lid: UUID,
    data: LoanMovementCreate,
) -> dict[str, object]:
    business, old = ops.begin(db, who, bid, data, f"loan.{lid}.movement")
    if old is not None:
        return old
    loan = repo.loan(db, bid, lid)
    payable, repay = loan.direction == "payable", data.kind == "repayment"
    principal = ops.money(data.principal, business, positive=False)
    interest = ops.money(data.interest, business, positive=False)
    fees = ops.money(data.fees, business, positive=False)
    if principal + interest + fees == 0 or (not repay and (interest or fees)):
        raise DomainError(
            "ERR_LOAN", "Drawdowns contain principal only; all amounts cannot be zero."
        )
    # Prevent a backdated repayment from making any later principal balance negative.
    movements = list(
        db.scalars(
            select(LoanMovement)
            .where(
                LoanMovement.business_id == bid,
                LoanMovement.loan_id == lid,
            )
            .order_by(LoanMovement.accounting_date, LoanMovement.id)
        )
    )
    if repay:
        days = sorted(
            {
                data.accounting_date,
                *(
                    m.accounting_date
                    for m in movements
                    if m.accounting_date >= data.accounting_date
                ),
            }
        )
        if any(repo.principal(db, bid, lid, day) < principal for day in days):
            raise DomainError("ERR_LOAN_BALANCE", "Repayment exceeds available principal.", 409)
    ops.account(db, bid, data.bank_account_id, {"asset"}, plain=True)
    lines = []
    if principal:
        lines.append(ops.line(loan.control_account_id, principal, debit=payable == repay))
    for value, aid in [(interest, data.interest_account_id), (fees, data.fee_account_id)]:
        if value:
            if aid is None:
                raise DomainError("ERR_LOAN", "Interest and fees require separate accounts.")
            ops.account(db, bid, aid, {"expense"} if payable else {"income"}, plain=True)
            lines.append(ops.line(aid, value, debit=payable))
    lines.append(
        ops.line(data.bank_account_id, principal + interest + fees, debit=payable != repay)
    )
    jid = ops.publish(db, who, business, data, data.accounting_date, "Loan " + data.kind, lines)
    item = LoanMovement(
        business_id=bid,
        loan_id=lid,
        accounting_date=data.accounting_date,
        kind=data.kind,
        principal=principal,
        interest=interest,
        fees=fees,
        journal_id=jid,
    )
    db.add(item)
    db.flush()
    return ops.finish(
        db,
        who,
        bid,
        data,
        f"loan.{lid}.movement",
        {"id": str(item.id), "journal_id": str(jid)},
        jid,
    )
