"""I2: Per-workspace auto-post policy (owner + MFA gated, off by default)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.audit.service import record
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize

AutoPostJobKind = Literal[
    "amortization_monthly",
    "accrual_day1_reversal",
    "receipt_auto_match",
    "intercompany_mirror",
]

ALL_AUTO_POST_KINDS: tuple[AutoPostJobKind, ...] = (
    "amortization_monthly",
    "accrual_day1_reversal",
    "receipt_auto_match",
    "intercompany_mirror",
)


def should_auto_post(
    db: Session,
    business_id: UUID,
    job_kind: AutoPostJobKind,
    amount: Decimal,
) -> tuple[bool, UUID | None]:
    """Return (allowed, policy_id) — False unless explicitly enabled and within max_amount."""
    row = (
        db.execute(
            text(
                """
                SELECT id, enabled, max_amount, mfa_verified
                FROM app.auto_post_policies
                WHERE business_id = :bid AND job_kind = :jk
                """
            ),
            {"bid": business_id, "jk": job_kind},
        )
        .mappings()
        .first()
    )
    if row is None or not bool(row["enabled"]) or not bool(row["mfa_verified"]):
        return False, None
    max_amt = Decimal(str(row["max_amount"])).quantize(Decimal("0.01"))
    if abs(amount).quantize(Decimal("0.01")) > max_amt:
        return False, UUID(str(row["id"]))
    return True, UUID(str(row["id"]))


def list_auto_post_policies(db: Session, business_id: UUID) -> list[dict[str, Any]]:
    """Return all auto-post policies for the workspace (defaulting unset kinds to disabled)."""
    rows = {
        str(r["job_kind"]): r
        for r in db.execute(
            text(
                """
                SELECT id, job_kind, enabled, max_amount, mfa_verified, enabled_by, enabled_at
                FROM app.auto_post_policies
                WHERE business_id = :bid
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    }
    out: list[dict[str, Any]] = []
    for kind in ALL_AUTO_POST_KINDS:
        r = rows.get(kind)
        if r is not None:
            out.append(
                {
                    "id": str(r["id"]),
                    "job_kind": kind,
                    "enabled": bool(r["enabled"]),
                    "max_amount": f"{Decimal(str(r['max_amount'])):.2f}",
                    "mfa_verified": bool(r["mfa_verified"]),
                    "enabled_at": r["enabled_at"].isoformat() if r["enabled_at"] else None,
                }
            )
        else:
            out.append(
                {
                    "id": None,
                    "job_kind": kind,
                    "enabled": False,
                    "max_amount": "5000.00",
                    "mfa_verified": False,
                    "enabled_at": None,
                }
            )
    return out


def upsert_auto_post_policy(
    db: Session,
    identity: Principal,
    business_id: UUID,
    *,
    job_kind: AutoPostJobKind,
    enabled: bool,
    max_amount: Decimal,
) -> dict[str, Any]:
    """Owner-only, MFA-gated toggle for unattended scheduled posting."""
    authorize(db, identity, business_id, {"owner"})
    if enabled and not identity.family.mfa:
        raise DomainError(
            "ERR_MFA_REQUIRED",
            "Two-factor authentication is required to enable automatic posting.",
            403,
        )
    capped = max_amount.quantize(Decimal("0.01"))
    if capped < Decimal("0.00"):
        raise DomainError("ERR_VALIDATION", "Maximum auto-post amount cannot be negative.", 422)

    pid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.auto_post_policies (
              id, business_id, job_kind, enabled, max_amount,
              enabled_by, enabled_at, mfa_verified, updated_at
            ) VALUES (
              :id, :bid, :jk, :en, :maxa,
              :uid, CASE WHEN :en THEN now() ELSE NULL END, :mfa, now()
            )
            ON CONFLICT (business_id, job_kind) DO UPDATE SET
              enabled = EXCLUDED.enabled,
              max_amount = EXCLUDED.max_amount,
              enabled_by = EXCLUDED.enabled_by,
              enabled_at = CASE
                WHEN EXCLUDED.enabled THEN now()
                ELSE app.auto_post_policies.enabled_at
              END,
              mfa_verified = EXCLUDED.mfa_verified,
              updated_at = now()
            """
        ),
        {
            "id": pid,
            "bid": business_id,
            "jk": job_kind,
            "en": enabled,
            "maxa": capped,
            "uid": identity.user.id,
            "mfa": bool(identity.family.mfa),
        },
    )
    record(
        db,
        identity.user.id,
        "workspace.auto_post_policy_updated",
        business_id,
        pid,
        {"job_kind": job_kind, "enabled": enabled, "max_amount": str(capped)},
    )
    return {
        "job_kind": job_kind,
        "enabled": enabled,
        "max_amount": f"{capped:.2f}",
        "mfa_verified": bool(identity.family.mfa),
    }
