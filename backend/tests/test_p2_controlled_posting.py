import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from ledgerpilot.banking.models import BankRow, BankRule
from ledgerpilot.banking.shadow import (
    controlled_posting,
    get_workspace_posting_control,
    set_workspace_posting_control,
)
from ledgerpilot.shared.errors import DomainError


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
        active=True,
        preview_hash="abc123hash",
    )
    mock_ctrl = patch(
        "ledgerpilot.banking.shadow.get_workspace_posting_control", return_value={"paused": False}
    )
    with mock_ctrl:
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


def test_controlled_posting_workspace_durable_pause() -> None:
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
        active=True,
        preview_hash="abc123hash",
    )
    with patch(
        "ledgerpilot.banking.shadow.get_workspace_posting_control",
        return_value={"paused": True, "reason": "Operator audit in progress"},
    ):
        dry_res = controlled_posting(
            db,
            bid,
            who_id,
            rule,
            max_amount=Decimal(100),
            dry_run=True,
        )
        assert dry_res["status"] == "workspace_paused"
        assert dry_res["reason"] == "Operator audit in progress"

        with pytest.raises(DomainError) as exc_info:
            controlled_posting(
                db,
                bid,
                who_id,
                rule,
                max_amount=Decimal(100),
                expected_rule_version=1,
                dry_run=False,
            )
        assert exc_info.value.code == "ERR_PAUSED"


def test_controlled_posting_version_and_activation_safeguards() -> None:
    db = MagicMock()
    bid = uuid4()
    who_id = uuid4()
    inactive_rule = BankRule(
        id=uuid4(),
        business_id=bid,
        name="Inactive rule",
        contains="test",
        account_id=uuid4(),
        version=2,
        active=False,
        preview_hash=None,
    )
    mock_ctrl = patch(
        "ledgerpilot.banking.shadow.get_workspace_posting_control", return_value={"paused": False}
    )
    with mock_ctrl:
        # 1. Version mismatch
        with pytest.raises(DomainError) as exc_info:
            controlled_posting(
                db,
                bid,
                who_id,
                inactive_rule,
                max_amount=Decimal(100),
                expected_rule_version=1,
                dry_run=True,
            )
        assert exc_info.value.code == "ERR_VERSION"

        # 2. Missing version on live run
        with pytest.raises(DomainError) as exc_info:
            controlled_posting(
                db,
                bid,
                who_id,
                inactive_rule,
                max_amount=Decimal(100),
                expected_rule_version=None,
                dry_run=False,
            )
        assert exc_info.value.code == "ERR_VERSION_REQUIRED"

        # 3. Inactive rule on live run
        with pytest.raises(DomainError) as exc_info:
            controlled_posting(
                db,
                bid,
                who_id,
                inactive_rule,
                max_amount=Decimal(100),
                expected_rule_version=2,
                dry_run=False,
            )
        assert exc_info.value.code == "ERR_RULE_INACTIVE"


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
        active=True,
        preview_hash="valid_hash_1",
    )

    target_account = MagicMock()
    target_account.kind = "expense"

    row_eligible = BankRow(
        id=uuid4(),
        business_id=bid,
        state="for_review",
        version=0,
        description="GitHub Sub",
        amount=Decimal("-45.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 1),
    )
    row_over_limit = BankRow(
        id=uuid4(),
        business_id=bid,
        state="for_review",
        version=0,
        description="GitHub Enterprise",
        amount=Decimal("-250.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 2),
    )
    row_excluded = BankRow(
        id=uuid4(),
        business_id=bid,
        state="for_review",
        version=0,
        description="GitHub Payroll Wire",
        amount=Decimal("-50.00"),
        account_id=uuid4(),
        accounting_date=datetime.date(2026, 11, 3),
    )

    db.scalars.return_value = [row_eligible, row_over_limit, row_excluded]

    mock_ctrl = patch(
        "ledgerpilot.banking.shadow.get_workspace_posting_control", return_value={"paused": False}
    )
    with (
        patch("ledgerpilot.banking.shadow.ops.account", return_value=target_account),
        patch("ledgerpilot.banking.shadow.get_business", return_value=MagicMock()),
        patch("ledgerpilot.banking.shadow.ops.money", side_effect=lambda v, _: Decimal(v)),
        mock_ctrl,
    ):
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
        active=True,
        preview_hash="valid_preview_hash",
    )

    target_account = MagicMock()
    target_account.kind = "expense"

    row_eligible = BankRow(
        id=uuid4(),
        business_id=bid,
        state="for_review",
        version=0,
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

    mock_ctrl = patch(
        "ledgerpilot.banking.shadow.get_workspace_posting_control", return_value={"paused": False}
    )
    with (
        patch("ledgerpilot.banking.shadow.ops.account", return_value=target_account),
        patch("ledgerpilot.banking.shadow.get_business", return_value=MagicMock()),
        patch("ledgerpilot.banking.shadow.ops.money", side_effect=lambda v, _: Decimal(v)),
        mock_ctrl,
        patch("ledgerpilot.banking.shadow.record") as mock_record,
    ):
        with pytest.raises(DomainError) as stale:
            controlled_posting(
                db,
                bid,
                who_id,
                rule,
                Decimal("100.00"),
                expected_rule_version=1,
                dry_run=False,
                expected_preview_hash="0" * 64,
            )
        assert stale.value.code == "ERR_PREVIEW_CHANGED"
        assert row_eligible.state == "for_review"
        result = controlled_posting(
            db,
            bid,
            who_id,
            rule,
            max_amount=Decimal("100.00"),
            expected_rule_version=1,
            start=datetime.date(2026, 11, 1),
            end=datetime.date(2026, 11, 30),
            paused=False,
            dry_run=False,
        )

    assert result["status"] == "posted"
    assert result["eligible_count"] == 1
    assert result["posted_count"] == 1
    assert result["posted_row_ids"] == [row_eligible.id]
    assert row_eligible.state == "categorized"
    assert row_eligible.journal_id == journal_id
    assert row_eligible.version == 1 and row_eligible.reason
    assert db.add.call_args.args[0].journal_id == journal_id

    mock_record.assert_called_once_with(
        db,
        who_id,
        "bank.auto_posted",
        bid,
        row_eligible.id,
        {
            "rule_id": str(rule.id),
            "rule_version": 1,
            "journal_id": str(journal_id),
            "amount": str(row_eligible.amount),
            "scope": {
                "start": "2026-11-01",
                "end": "2026-11-30",
                "max_amount": "100.00",
            },
            "exclusions": ["payroll", "tax", "wire", "loan", "draw"],
        },
    )


def test_set_and_get_workspace_posting_control() -> None:
    db = MagicMock()
    bid = uuid4()
    who_id = uuid4()

    with patch("ledgerpilot.banking.shadow.record"):
        res = set_workspace_posting_control(
            db, bid, who_id, paused=True, reason="Maintenance drill"
        )
        assert res["paused"] is True
        assert res["reason"] == "Maintenance drill"

        db.execute.return_value.mappings.return_value.first.return_value = {
            "action": "workspace.auto_posting_paused",
            "user_id": who_id,
            "occurred_at": datetime.datetime.now(datetime.UTC),
            "details": {"reason": "Maintenance drill"},
        }
        ctrl = get_workspace_posting_control(db, bid)
        assert ctrl["paused"] is True
        assert ctrl["reason"] == "Maintenance drill"
