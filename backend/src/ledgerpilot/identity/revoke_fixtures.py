"""Fixture and test identity revocation for production traffic spike hardening (C2).

Disables fixture/test accounts, revokes active sessions and refresh tokens,
deactivates business memberships, and writes immutable audit events.
Safely defaults to --dry-run unless --force is explicitly provided.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import create_engine, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from ledgerpilot.audit.models import AuditEvent
from ledgerpilot.config import get_settings
from ledgerpilot.database import set_context
from ledgerpilot.identity.models import ActionToken, AuthSession, RefreshToken, User
from ledgerpilot.tenancy.models import Membership

DEFAULT_FIXTURE_PATTERNS = [
    "%@balanc.local",
    "%@acme.test",
    "%@example.com",
    "%@example.org",
    "%@example.net",
    "%@test.local",
    "%@balanc.test",
]

DEFAULT_FIXTURE_EMAILS = [
    "owner@balanc.local",
    "accountant@balanc.local",
    "owner@example.com",
    "accountant@example.com",
]

DEFAULT_AUDIT_REASON = "Production rollout: fixture account revocation per C2 hardening policy"


@dataclass
class UserRevocationPlan:
    user_id: UUID
    email: str
    already_suspended: bool
    active_sessions_count: int
    active_tokens_count: int
    active_action_tokens_count: int
    active_memberships: list[UUID]  # list of business_ids


@dataclass
class RevocationSummary:
    dry_run: bool
    reason: str
    users_matched: int
    users_suspended: int
    sessions_revoked: int
    refresh_tokens_revoked: int
    action_tokens_revoked: int
    memberships_deactivated: int
    audit_events_created: int
    details: list[UserRevocationPlan] = field(default_factory=list)


def inspect_fixture_account(db: Session, user: User) -> UserRevocationPlan:
    """Collect all active artifacts for a user."""
    # Active sessions
    sessions = db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        )
    ).all()

    # Active refresh tokens
    refresh_tokens = db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.used_at.is_(None),
        )
    ).all()

    # Active action tokens
    action_tokens = db.scalars(
        select(ActionToken).where(
            ActionToken.user_id == user.id,
            ActionToken.used_at.is_(None),
        )
    ).all()

    # Active memberships
    memberships = db.scalars(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.active.is_(True),
        )
    ).all()

    return UserRevocationPlan(
        user_id=user.id,
        email=user.email,
        already_suspended=user.suspended,
        active_sessions_count=len(sessions),
        active_tokens_count=len(refresh_tokens),
        active_action_tokens_count=len(action_tokens),
        active_memberships=[m.business_id for m in memberships],
    )


def find_fixture_users(
    db: Session,
    patterns: Sequence[str] = DEFAULT_FIXTURE_PATTERNS,
    explicit_emails: Sequence[str] = DEFAULT_FIXTURE_EMAILS,
) -> list[User]:
    """Discover fixture users via direct match and scoped RLS lookup."""
    user_map: dict[UUID, User] = {}

    # Attempt pattern match (works under admin/migration role or non-RLS)
    try:
        conditions = [User.email.ilike(p) for p in patterns]
        if explicit_emails:
            conditions.append(User.email.in_(explicit_emails))
        stmt = select(User).where(or_(*conditions))
        matched = db.scalars(stmt).all()
        for u in matched:
            user_map[u.id] = u
    except Exception:
        # If RLS prevents blanket select, fallback to explicit email check
        db.rollback()

    # Explicit email lookup with transaction-local context
    for email in explicit_emails:
        try:
            db.execute(text("SELECT set_config('app.login_email', :email, true)"), {"email": email})
            user_opt: User | None = db.scalar(select(User).where(User.email == email))
            if user_opt and user_opt.id not in user_map:
                user_map[user_opt.id] = user_opt
        except Exception:
            db.rollback()

    return list(user_map.values())


def revoke_fixtures(
    db: Session,
    *,
    dry_run: bool = True,
    reason: str = DEFAULT_AUDIT_REASON,
    patterns: Sequence[str] = DEFAULT_FIXTURE_PATTERNS,
    explicit_emails: Sequence[str] = DEFAULT_FIXTURE_EMAILS,
) -> RevocationSummary:
    """Plan or execute revocation of fixture accounts, sessions, and memberships."""
    users = find_fixture_users(db, patterns=patterns, explicit_emails=explicit_emails)

    plans: list[UserRevocationPlan] = []
    for user in users:
        plans.append(inspect_fixture_account(db, user))

    summary = RevocationSummary(
        dry_run=dry_run,
        reason=reason,
        users_matched=len(plans),
        users_suspended=sum(1 for p in plans if not p.already_suspended),
        sessions_revoked=sum(p.active_sessions_count for p in plans),
        refresh_tokens_revoked=sum(p.active_tokens_count for p in plans),
        action_tokens_revoked=sum(p.active_action_tokens_count for p in plans),
        memberships_deactivated=sum(len(p.active_memberships) for p in plans),
        audit_events_created=0,
        details=plans,
    )

    if dry_run or not plans:
        return summary

    now = datetime.now(UTC)
    audit_events_count = 0

    for plan in plans:
        # Establish transaction context for RLS compliance
        set_context(db, user_id=plan.user_id)

        # 1. Suspend User
        target_user: User | None = db.scalar(
            select(User).where(User.id == plan.user_id).with_for_update()
        )
        if target_user:
            target_user.suspended = True

        # 2. Revoke Sessions
        sessions = db.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == plan.user_id,
                AuthSession.revoked_at.is_(None),
            )
            .with_for_update()
        ).all()
        for s in sessions:
            s.revoked_at = now

        # 3. Invalidate Refresh Tokens
        tokens = db.scalars(
            select(RefreshToken)
            .where(
                RefreshToken.user_id == plan.user_id,
                RefreshToken.used_at.is_(None),
            )
            .with_for_update()
        ).all()
        for t in tokens:
            t.used_at = now

        # 4. Invalidate Action Tokens (password resets, invites)
        action_tokens = db.scalars(
            select(ActionToken)
            .where(
                ActionToken.user_id == plan.user_id,
                ActionToken.used_at.is_(None),
            )
            .with_for_update()
        ).all()
        for at in action_tokens:
            at.used_at = now

        # 5. Revoke Business Memberships & write scoped audit events
        for biz_id in plan.active_memberships:
            set_context(db, user_id=plan.user_id, business_id=biz_id)
            # Record workspace-level audit event while membership is still valid
            biz_audit = AuditEvent(
                user_id=plan.user_id,
                business_id=biz_id,
                action="identity.revocation_batch",
                resource_id=plan.user_id,
                details={
                    "reason": reason,
                    "target_email": plan.email,
                    "revocation_type": "membership_deactivated",
                    "timestamp": now.isoformat(),
                },
            )
            db.add(biz_audit)
            audit_events_count += 1

            membership = db.scalar(
                select(Membership)
                .where(
                    Membership.business_id == biz_id,
                    Membership.user_id == plan.user_id,
                )
                .with_for_update()
            )
            if membership:
                membership.active = False

        # 6. Global Identity Audit Event
        set_context(db, user_id=plan.user_id, business_id=None)
        user_audit = AuditEvent(
            user_id=plan.user_id,
            business_id=None,
            action="identity.revocation_batch",
            resource_id=plan.user_id,
            details={
                "reason": reason,
                "target_email": plan.email,
                "revoked_sessions": plan.active_sessions_count,
                "revoked_refresh_tokens": plan.active_tokens_count,
                "revoked_action_tokens": plan.active_action_tokens_count,
                "deactivated_memberships": len(plan.active_memberships),
                "timestamp": now.isoformat(),
            },
        )
        db.add(user_audit)
        audit_events_count += 1

    db.commit()
    summary.audit_events_created = audit_events_count
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Revoke fixture and test accounts before traffic rollout (C2 hardening)."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--force",
        action="store_true",
        help="Commit revocation changes to the database. Without this, runs in dry-run mode.",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Simulate revocation and report targets without writing changes (default).",
    )
    parser.add_argument(
        "--audit-reason",
        type=str,
        default=DEFAULT_AUDIT_REASON,
        help="Reason for revocation recorded in immutable audit events.",
    )
    parser.add_argument(
        "--email",
        action="append",
        dest="emails",
        help="Specific email address to target (can be specified multiple times).",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        dest="patterns",
        help="SQL LIKE pattern for email matching (can be specified multiple times).",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Optional database connection string. Defaults to application settings.",
    )

    args = parser.parse_args(argv)
    dry_run = not args.force

    settings = get_settings()
    db_url = args.database_url or settings.database_url.get_secret_value()
    engine = create_engine(db_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine)

    patterns = args.patterns or DEFAULT_FIXTURE_PATTERNS
    emails = args.emails or DEFAULT_FIXTURE_EMAILS

    mode_label = "DRY-RUN (No changes applied)" if dry_run else "LIVE EXECUTION (Committed)"
    print("=== LedgerPilot Fixture Revocation Tool ===")
    print(f"Mode: {mode_label}")
    print(f"Audit Reason: {args.audit_reason}")
    print(f"Target Patterns: {patterns}")
    print(f"Explicit Emails: {emails}")
    print("-" * 50)

    with factory() as db:
        summary = revoke_fixtures(
            db,
            dry_run=dry_run,
            reason=args.audit_reason,
            patterns=patterns,
            explicit_emails=emails,
        )

    print(f"Matched Users:         {summary.users_matched}")
    print(f"Users Suspended:       {summary.users_suspended}")
    print(f"Sessions Revoked:      {summary.sessions_revoked}")
    print(f"Refresh Tokens Revoked: {summary.refresh_tokens_revoked}")
    print(f"Action Tokens Revoked:  {summary.action_tokens_revoked}")
    print(f"Memberships Deactivated:{summary.memberships_deactivated}")
    print(f"Audit Events Emitted:  {summary.audit_events_created}")
    print("-" * 50)

    for p in summary.details:
        status = "already suspended" if p.already_suspended else "active -> SUSPENDED"
        print(
            f"  * {p.email} ({p.user_id}): {status} | "
            f"{p.active_sessions_count} sessions, {p.active_tokens_count} tokens, "
            f"{len(p.active_memberships)} memberships"
        )

    if dry_run:
        print("\nNotice: Safe dry-run completed. Run with '--force' to apply changes.")
    else:
        print("\nSuccess: Fixture accounts and credentials revoked successfully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
