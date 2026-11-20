"""Resolve membership before binding any business scope."""

import contextlib
import json
import time
from datetime import date, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import set_context
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.identity.mail import enqueue_mail
from ledgerpilot.identity.security import canonical_email, digest, now, opaque
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import bind_identity
from ledgerpilot.tenancy.models import Business, Invitation, Membership, PolicyHistory
from ledgerpilot.tenancy.schemas import CURRENCIES, BusinessCreate, InviteCreate


def has_paid_subscription(db: Session, business_id: UUID) -> bool:
    """Return True when the business has an active/trialing or in-grace Stripe subscription."""
    stripe_conn = db.execute(
        text("SELECT app.stripe_entitlement(:b)"),
        {"b": business_id},
    ).scalar_one_or_none()
    if not stripe_conn:
        return False
    if isinstance(stripe_conn, str):
        with contextlib.suppress(Exception):
            stripe_conn = json.loads(stripe_conn)
    if not isinstance(stripe_conn, dict):
        return False
    status = stripe_conn.get("status")
    grace_until = stripe_conn.get("grace_until")
    if status in {"active", "trialing"}:
        return True
    if status == "past_due" and grace_until is not None:
        return time.time() < float(grace_until)
    if status in {"canceled", "unpaid", "expired"}:
        return False
    return bool(stripe_conn.get("paid_access", False)) and (
        grace_until is None or time.time() < float(grace_until)
    )


def allows_feature(db: Session, settings: Settings, business_id: UUID, feature: str) -> bool:
    """Unlock all workspace features when the business holds an active paid subscription."""
    if settings.app_env == "demo":
        return False
    checker = getattr(settings, f"allows_p2_{feature}")
    if checker(business_id):
        return True
    return has_paid_subscription(db, business_id)


def authorize(
    db: Session,
    identity: Principal,
    business_id: UUID,
    roles: set[str] | None = None,
    posting: bool = False,
) -> Membership:
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == identity.user.id,
            Membership.business_id == business_id,
            Membership.active.is_(True),
        )
    )
    if not membership:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    bind_identity(identity.user.id, business_id)
    if (membership.role in {"owner", "accountant"} or posting) and not identity.family.mfa:
        raise DomainError("ERR_MFA_REQUIRED", "MFA authentication is required.", 403)
    if roles is not None and membership.role not in roles:
        raise DomainError("ERR_FORBIDDEN", "Permission denied.", 403)
    if posting and membership.role != "accountant" and not membership.can_post:
        raise DomainError("ERR_FORBIDDEN", "Posting permission is required.", 403)
    set_context(db, identity.user.id, business_id)
    rate_scope = (identity.user.id, business_id)
    rate_factory = db.info.get("rate_factory")
    if rate_factory is not None and db.info.get("admitted_scope") != rate_scope:
        from ledgerpilot.shared.middleware import rate_limited

        delay = rate_limited(
            rate_factory,
            [
                (digest(f"business:{business_id}"), db.info.get("rate_workspace_budget", 600)),
                (
                    digest(f"business-user:{business_id}:{identity.user.id}"),
                    db.info.get("rate_workspace_member_budget", 120),
                ),
            ],
            window_seconds=db.info.get("rate_window_seconds", 60),
            cooldown_max_seconds=db.info.get("rate_cooldown_max_seconds", 60),
        )
        if delay:
            raise DomainError(
                "ERR_RATE_LIMIT",
                "Business request limit reached. Retry shortly.",
                429,
                retry_after=delay,
            )
        db.info["admitted_scope"] = rate_scope
    if db.info.get("trial_scope") != business_id:
        db.execute(text("SELECT app.initialize_trial(:b)"), {"b": business_id})
        db.info["trial_scope"] = business_id
    if db.info.get("http_method") in {"POST", "PATCH"}:
        from ledgerpilot.workspace.models import TrialEntitlement

        trial = db.scalar(
            select(TrialEntitlement).where(TrialEntitlement.business_id == business_id)
        )
        path = db.info.get("request_path", "")
        retained_action = path.endswith(
            (
                "/privacy-requests",
                "/consents",
                "/preview",
                "/read",
                "/exports/snapshots",
                "/billing/checkout",
                "/billing/reconcile",
                "/billing/subscription/plan",
                "/billing/subscription/cancel",
                "/billing/subscription/resume",
            )
        )
        retained_action = retained_action or (
            f"/businesses/{business_id}/privacy-requests/" in path and path.endswith("/reviews")
        )
        # Retention obligations remain manageable when the commercial trial expires.
        # Each retention route separately requires an owner/accountant MFA session.
        retained_action = retained_action or f"/businesses/{business_id}/retention/" in path
        retained_action = retained_action or (
            f"/businesses/{business_id}/audit-grants/" in path and path.endswith("/revoke")
        )
        has_paid_access = has_paid_subscription(db, business_id)

        trial_expired = trial is not None and (
            trial.status != "manual_trial"
            or (trial.expires_at is not None and trial.expires_at <= now())
        )
        if trial_expired and not has_paid_access and not retained_action:
            raise DomainError(
                "ERR_ENTITLEMENT",
                "This business currently permits read/export and privacy access only.",
                403,
            )
    if (
        db.info.get("http_method") == "GET"
        and not db.info.get("consistent_snapshot")
        and db.info.get("snapshot_scope") != business_id
    ):
        # The bounded local profile assembles reports under a shared business lock.
        # Canonical financial writers take FOR UPDATE, so totals cannot mix versions.
        db.execute(text("SELECT id FROM app.businesses WHERE id=:b FOR SHARE"), {"b": business_id})
        db.info["snapshot_scope"] = business_id
    return membership


def create(
    db: Session,
    identity: Principal,
    data: BusinessCreate,
    settings: Settings | None = None,
) -> UUID:
    if not identity.family.mfa:
        raise DomainError("ERR_MFA_REQUIRED", "MFA authentication is required.", 403)
    from ledgerpilot.provider_integrations import (
        attach_onboarding_subscription,
        resolve_onboarding_subscription_for_business,
    )

    onboarding = resolve_onboarding_subscription_for_business(
        db, settings, identity, data.checkout_session_id
    )
    if settings is not None and settings.require_plan_before_business and onboarding is None:
        raise DomainError(
            "ERR_SUBSCRIPTION_REQUIRED",
            "Purchase a yearly subscription plan before creating a business.",
            402,
        )
    bid = uuid4()
    db.execute(
        text("""SELECT app.create_business(:id,:name,:currency,:precision,
                   :timezone,:basis,:fiscal,:jurisdiction)"""),
        {
            "id": bid,
            "name": data.name,
            "currency": data.currency,
            "precision": CURRENCIES[data.currency],
            "timezone": data.timezone,
            "basis": data.basis,
            "fiscal": data.fiscal_month,
            "jurisdiction": data.jurisdiction,
        },
    )
    if onboarding is not None:
        set_context(db, identity.user.id, bid)
        attach_onboarding_subscription(db, settings, bid, onboarding)
    record(db, identity.user.id, "business.created", bid, bid)
    return bid


def invite(
    db: Session, settings: Settings, identity: Principal, bid: UUID, data: InviteCreate
) -> None:
    member = authorize(db, identity, bid, {"owner", "accountant"})
    if member.role != "owner" and (not member.can_invite or data.role == "owner"):
        raise DomainError("ERR_FORBIDDEN", "Invitation permission is required.", 403)
    token = opaque()
    email = canonical_email(data.email)
    db.add(
        Invitation(
            token_hash=digest(token),
            business_id=bid,
            email=email,
            role=data.role,
            expires_at=now() + timedelta(hours=48),
        )
    )
    enqueue_mail(
        db, settings, email, "business invitation", token, user_id=identity.user.id, business_id=bid
    )
    record(db, identity.user.id, "business.invited", bid)


def accept(db: Session, identity: Principal, token: str) -> UUID:
    if not identity.family.mfa:
        raise DomainError("ERR_MFA_REQUIRED", "MFA authentication is required.", 403)
    try:
        bid = db.execute(
            text("SELECT app.accept_invite(:h)"), {"h": digest(token)}
        ).scalar_one_or_none()
    except DBAPIError as error:
        message = getattr(getattr(error.orig, "diag", None), "message_primary", "")
        if message == "Invitation is invalid or expired":
            raise DomainError(
                "ERR_LINK_EXPIRED", "Invitation is invalid or expired.", 410
            ) from None
        raise
    if bid is None:
        raise DomainError("ERR_LINK_EXPIRED", "Invitation is invalid or expired.", 410)
    record(db, identity.user.id, "business.invitation_accepted", bid)
    return bid


def get_business(db: Session, bid: UUID, lock: bool = False) -> Business:
    query = select(Business).where(Business.id == bid)
    if lock:
        query = query.with_for_update()
    business = db.scalar(query)
    if not business:
        raise DomainError("ERR_NOT_FOUND", "Record not found.", 404)
    return business


def effective_policy(
    db: Session, business: Business, as_of: date | None = None
) -> PolicyHistory | None:
    business_date = as_of or now().astimezone(ZoneInfo(business.timezone)).date()
    return db.scalar(
        select(PolicyHistory)
        .where(
            PolicyHistory.business_id == business.id, PolicyHistory.effective_date <= business_date
        )
        .order_by(PolicyHistory.effective_date.desc(), PolicyHistory.version.desc())
        .limit(1)
    )
