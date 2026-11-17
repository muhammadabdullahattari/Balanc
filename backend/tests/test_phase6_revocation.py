"""Phase 6: Fixture Account Revocation (C2 Residual) verification.

Covers:
- Safe dry-run inspection of test/fixture accounts and active credentials.
- Forced execution: sets user.suspended=True, invalidates sessions and tokens.
- Immutable audit trail generation (identity.revocation_batch).
- CLI argument parsing, flags (--dry-run, --force, etc.), and exit codes.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from ledgerpilot.audit.models import AuditEvent
from ledgerpilot.identity.models import ActionToken, AuthSession, RefreshToken, User
from ledgerpilot.identity.revoke_fixtures import (
    DEFAULT_AUDIT_REASON,
    DEFAULT_FIXTURE_EMAILS,
    DEFAULT_FIXTURE_PATTERNS,
    RevocationSummary,
    UserRevocationPlan,
    inspect_fixture_account,
    main,
    revoke_fixtures,
)
from ledgerpilot.tenancy.models import Membership


def test_fixture_defaults_include_known_seeded_patterns_and_emails() -> None:
    """Verify default lists contain known test and seed identities."""
    assert "%@balanc.local" in DEFAULT_FIXTURE_PATTERNS
    assert "%@acme.test" in DEFAULT_FIXTURE_PATTERNS
    assert "%@example.com" in DEFAULT_FIXTURE_PATTERNS
    assert "%@example.org" in DEFAULT_FIXTURE_PATTERNS
    assert "%@example.net" in DEFAULT_FIXTURE_PATTERNS
    assert "%@test.local" in DEFAULT_FIXTURE_PATTERNS
    assert "%@balanc.test" in DEFAULT_FIXTURE_PATTERNS

    assert "owner@balanc.local" in DEFAULT_FIXTURE_EMAILS
    assert "accountant@balanc.local" in DEFAULT_FIXTURE_EMAILS
    assert "owner@example.com" in DEFAULT_FIXTURE_EMAILS
    assert "accountant@example.com" in DEFAULT_FIXTURE_EMAILS


def test_inspect_fixture_account_counts_active_artifacts() -> None:
    """inspect_fixture_account counts only unrevoked sessions, tokens, and active memberships."""
    user_id = uuid4()
    biz_id1 = uuid4()
    biz_id2 = uuid4()

    mock_user = MagicMock(spec=User)
    mock_user.id = user_id
    mock_user.email = "test@balanc.local"
    mock_user.suspended = False

    active_s1 = MagicMock(spec=AuthSession, user_id=user_id, revoked_at=None)
    active_s2 = MagicMock(spec=AuthSession, user_id=user_id, revoked_at=None)

    active_t1 = MagicMock(spec=RefreshToken, user_id=user_id, used_at=None)
    active_at1 = MagicMock(spec=ActionToken, user_id=user_id, used_at=None)

    active_m1 = MagicMock(spec=Membership, user_id=user_id, business_id=biz_id1, active=True)
    active_m2 = MagicMock(spec=Membership, user_id=user_id, business_id=biz_id2, active=True)

    db = MagicMock()
    # Configure db.scalars().all() return values sequentially
    db.scalars.return_value.all.side_effect = [
        [active_s1, active_s2],  # sessions
        [active_t1],  # refresh tokens
        [active_at1],  # action tokens
        [active_m1, active_m2],  # memberships
    ]

    plan = inspect_fixture_account(db, mock_user)

    assert plan.user_id == user_id
    assert plan.email == "test@balanc.local"
    assert plan.already_suspended is False
    assert plan.active_sessions_count == 2
    assert plan.active_tokens_count == 1
    assert plan.active_action_tokens_count == 1
    assert plan.active_memberships == [biz_id1, biz_id2]


def test_revoke_fixtures_dry_run_leaves_database_untouched() -> None:
    """In dry-run mode, findings are reported but no updates or audit events are committed."""
    user1_id = uuid4()
    user2_id = uuid4()
    biz_id = uuid4()

    u1 = MagicMock(spec=User, id=user1_id, email="owner@balanc.local", suspended=False)
    u2 = MagicMock(spec=User, id=user2_id, email="accountant@example.com", suspended=False)

    db = MagicMock()
    plan1 = UserRevocationPlan(
        user_id=user1_id,
        email="owner@balanc.local",
        already_suspended=False,
        active_sessions_count=2,
        active_tokens_count=1,
        active_action_tokens_count=0,
        active_memberships=[biz_id],
    )
    plan2 = UserRevocationPlan(
        user_id=user2_id,
        email="accountant@example.com",
        already_suspended=False,
        active_sessions_count=1,
        active_tokens_count=1,
        active_action_tokens_count=1,
        active_memberships=[],
    )

    with (
        patch("ledgerpilot.identity.revoke_fixtures.find_fixture_users", return_value=[u1, u2]),
        patch(
            "ledgerpilot.identity.revoke_fixtures.inspect_fixture_account",
            side_effect=[plan1, plan2],
        ),
    ):
        summary = revoke_fixtures(db, dry_run=True, reason="Pre-launch verification")

    assert summary.dry_run is True
    assert summary.users_matched == 2
    assert summary.users_suspended == 2
    assert summary.sessions_revoked == 3
    assert summary.refresh_tokens_revoked == 2
    assert summary.action_tokens_revoked == 1
    assert summary.memberships_deactivated == 1
    assert summary.audit_events_created == 0

    # Ensure no DB commit or modifications occurred
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_revoke_fixtures_force_mode_commits_suspension_invalidation_and_audit() -> None:
    """In force mode, accounts are suspended, sessions/tokens invalidated, memberships deactivated,
    and immutable audit records are committed.
    """
    user_id = uuid4()
    biz_id = uuid4()

    mock_user = MagicMock(spec=User, id=user_id, email="owner@balanc.local", suspended=False)
    mock_session = MagicMock(spec=AuthSession, user_id=user_id, revoked_at=None)
    mock_refresh = MagicMock(spec=RefreshToken, user_id=user_id, used_at=None)
    mock_action = MagicMock(spec=ActionToken, user_id=user_id, used_at=None)
    mock_membership = MagicMock(spec=Membership, user_id=user_id, business_id=biz_id, active=True)

    db = MagicMock()

    # db.scalar returns user on first call, membership on next
    db.scalar.side_effect = [mock_user, mock_membership]
    # db.scalars().all() returns active sessions, refresh tokens, action tokens
    db.scalars.return_value.all.side_effect = [
        [mock_session],
        [mock_refresh],
        [mock_action],
    ]

    plan = UserRevocationPlan(
        user_id=user_id,
        email="owner@balanc.local",
        already_suspended=False,
        active_sessions_count=1,
        active_tokens_count=1,
        active_action_tokens_count=1,
        active_memberships=[biz_id],
    )

    with (
        patch("ledgerpilot.identity.revoke_fixtures.find_fixture_users", return_value=[mock_user]),
        patch("ledgerpilot.identity.revoke_fixtures.inspect_fixture_account", return_value=plan),
        patch("ledgerpilot.identity.revoke_fixtures.set_context"),
    ):
        summary = revoke_fixtures(
            db,
            dry_run=False,
            reason="Hardening cutover C2",
        )

    # 1. User suspended
    assert mock_user.suspended is True

    # 2. Session revoked
    assert mock_session.revoked_at is not None

    # 3. Tokens marked used
    assert mock_refresh.used_at is not None
    assert mock_action.used_at is not None

    # 4. Membership deactivated
    assert mock_membership.active is False

    # 5. Audit events added: 1 for business, 1 for global user
    assert db.add.call_count == 2
    added_events = [c[0][0] for c in db.add.call_args_list]

    # Verify workspace-scoped audit event
    biz_event = added_events[0]
    assert isinstance(biz_event, AuditEvent)
    assert biz_event.business_id == biz_id
    assert biz_event.action == "identity.revocation_batch"
    assert biz_event.details["revocation_type"] == "membership_deactivated"
    assert biz_event.details["reason"] == "Hardening cutover C2"

    # Verify global identity audit event
    user_event = added_events[1]
    assert isinstance(user_event, AuditEvent)
    assert user_event.business_id is None
    assert user_event.action == "identity.revocation_batch"
    assert user_event.details["revoked_sessions"] == 1
    assert user_event.details["revoked_refresh_tokens"] == 1
    assert user_event.details["deactivated_memberships"] == 1

    # 6. Committed
    db.commit.assert_called_once()
    assert summary.dry_run is False
    assert summary.audit_events_created == 2


def test_cli_argument_parsing_and_execution() -> None:
    """Verify CLI flags for dry-run (default), force, audit reason, and exit code."""
    with (
        patch("ledgerpilot.identity.revoke_fixtures.get_settings") as mock_settings,
        patch("ledgerpilot.identity.revoke_fixtures.create_engine"),
        patch("ledgerpilot.identity.revoke_fixtures.sessionmaker") as mock_maker,
    ):
        mock_settings.return_value.database_url.get_secret_value.return_value = (
            "postgresql+psycopg://test:pass@localhost:5432/testdb"
        )
        mock_session = MagicMock()
        mock_maker.return_value.return_value.__enter__.return_value = mock_session

        with patch("ledgerpilot.identity.revoke_fixtures.revoke_fixtures") as mock_revoke:
            mock_revoke.return_value = RevocationSummary(
                dry_run=True,
                reason=DEFAULT_AUDIT_REASON,
                users_matched=1,
                users_suspended=1,
                sessions_revoked=1,
                refresh_tokens_revoked=1,
                action_tokens_revoked=0,
                memberships_deactivated=0,
                audit_events_created=0,
                details=[],
            )

            # Default invocation (dry-run)
            exit_code = main([])
            assert exit_code == 0
            mock_revoke.assert_called_once()
            assert mock_revoke.call_args[1]["dry_run"] is True

            # Force invocation
            mock_revoke.reset_mock()
            mock_revoke.return_value.dry_run = False
            exit_code_force = main(["--force", "--audit-reason", "Production Drill"])
            assert exit_code_force == 0
            assert mock_revoke.call_args[1]["dry_run"] is False
            assert mock_revoke.call_args[1]["reason"] == "Production Drill"
