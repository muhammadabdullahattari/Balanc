import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from ledgerpilot.banking.models import BankRow, BankRule
from ledgerpilot.banking.shadow import controlled_posting


def test_controlled_posting_paused() -> None:
    db = MagicMock()
    bid = uuid4()
    who_id = uuid4()
    rule = BankRule(
        id=uuid4(),
        business_id=bid,
        name="Software rule",
        contains="software",
        account_id=uuid4(),
        version=1,
    )
    result = controlled_posting(
        db,
        bid,
        who_id,
        rule,
        max_amount=Decimal(100),
        paused=True,
    )
    assert result["status"] == "paused"
    assert result["rule_id"] == rule.id
    assert result["posted_count"] == 0
    assert result["eligible_count"] == 0


def test_controlled_posting_dry_run_flags_amounts_and_exclusions() -> None:
    db = MagicMock()
    bid = uuid4()
    who_id = uuid4()
    expense_account_id = uuid4()
    rule = BankRule(
        id=uuid4(),
        business_id=bid,
        name="Vendor rule",
        contains="github",
        account_id=expense_account_id,
        version=1,
    )

    # Setup mock target account and business
    target_account = MagicMock()
    target_account.kind = "expense"

    row_eligible = BankRow(
        id=uuid4(),
        business_id=bid,
        state="imported",
        description="GitHub Sub",
        amount=Decimal("-45.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 1),
    )
    row_over_limit = BankRow(
        id=uuid4(),
        business_id=bid,
        state="imported",
        description="GitHub Enterprise",
        amount=Decimal("-250.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 2),
    )
    row_excluded = BankRow(
        id=uuid4(),
        business_id=bid,
        state="imported",
        description="GitHub Payroll Wire",
        amount=Decimal("-50.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 3),
    )

    db.scalars.return_value = [row_eligible, row_over_limit, row_excluded]

    with patch("ledgerpilot.banking.shadow.ops.account", return_value=target_account), \
         patch("ledgerpilot.banking.shadow.get_business", return_value=MagicMock()), \
         patch("ledgerpilot.banking.shadow.ops.money", side_effect=lambda v, _: Decimal(v)):
        result = controlled_posting(
            db,
            bid,
            who_id,
            rule,
            max_amount=Decimal("100.00"),
            exclusions=["payroll", "tax", "wire"],
            paused=False,
            dry_run=True,
        )

    assert result["status"] == "dry_run"
    assert result["eligible_count"] == 1
    assert result["flagged_amount_count"] == 1
    assert result["flagged_amount_row_ids"] == [row_over_limit.id]
    assert result["flagged_exclusion_count"] == 1
    assert result["flagged_exclusion_row_ids"] == [row_excluded.id]
    assert result["posted_count"] == 0


def test_controlled_posting_live_execution() -> None:
    db = MagicMock()
    bid = uuid4()
    who_id = uuid4()
    expense_account_id = uuid4()
    rule = BankRule(
        id=uuid4(),
        business_id=bid,
        name="AWS Hosting",
        contains="aws",
        account_id=expense_account_id,
        version=1,
    )

    target_account = MagicMock()
    target_account.kind = "expense"

    row_eligible = BankRow(
        id=uuid4(),
        business_id=bid,
        state="imported",
        description="AWS Hosting Charge",
        amount=Decimal("-75.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 10),
    )

    db.scalars.return_value = [row_eligible]
    db.execute.return_value.mappings.return_value.one.return_value = {
        "version": 3,
        "currency": "USD",
    }
    journal_id = uuid4()
    db.execute.return_value.scalar_one.return_value = journal_id

    with patch("ledgerpilot.banking.shadow.ops.account", return_value=target_account), \
         patch("ledgerpilot.banking.shadow.get_business", return_value=MagicMock()), \
         patch("ledgerpilot.banking.shadow.ops.money", side_effect=lambda v, _: Decimal(v)), \
         patch("ledgerpilot.banking.shadow.record") as mock_record:
        result = controlled_posting(
            db,
            bid,
            who_id,
            rule,
            max_amount=Decimal("100.00"),
            paused=False,
            dry_run=False,
        )

    assert result["status"] == "posted"
    assert result["eligible_count"] == 1
    assert result["posted_count"] == 1
    assert result["posted_row_ids"] == [row_eligible.id]
    assert row_eligible.state == "categorized"
    assert row_eligible.journal_id == journal_id

    mock_record.assert_called_once_with(
        db,
        who_id,
        "bank.auto_posted",
        bid,
        row_eligible.id,
        {
            "rule_id": str(rule.id),
            "journal_id": str(journal_id),
            "amount": str(row_eligible.amount),
        },
    )
