"""Tenant operations request contracts."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from ledgerpilot.shared.schemas import Schema


class PreferencesUpdate(Schema):
    expected_version: int = Field(ge=0)
    marketing: bool = False
    reminders: bool = True
    quiet_start: int = Field(default=21, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)


class ReminderRequest(Schema):
    as_of: date


class TaskCreate(Schema):
    period_id: UUID
    name: str = Field(min_length=1, max_length=160)
    assigned_to: UUID
    required: bool = True


class TaskUpdate(Schema):
    expected_version: int = Field(ge=0)
    state: Literal["open", "prepared", "approved"]
    evidence_id: UUID | None = None
    comment: str = Field(default="", max_length=2000)


class ConsentCreate(Schema):
    purpose: Literal["terms", "privacy", "marketing", "analytics"]
    policy_version: str = Field(min_length=1, max_length=80)
    granted: bool


class PrivacyCreate(Schema):
    kind: Literal["export", "deletion"]
    reason: str = Field(min_length=1, max_length=1000)


class AssistantRequest(Schema):
    report_id: UUID
    metric: Literal["income", "expense", "net_profit", "total", "balanced", "retained_earnings"]


class MembershipUpdate(Schema):
    expected_version: int = Field(ge=0)
    role: Literal["owner", "accountant", "bookkeeper", "reviewer", "auditor"]
    active: bool
    can_post: bool = False
    can_invite: bool = False


class ReasonRequest(Schema):
    reason: str = Field(min_length=1, max_length=1000)
