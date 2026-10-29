"""Explicit tenant operations models; financial records remain in source modules."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ledgerpilot.database import Base
from ledgerpilot.trade.models import TenantRecord


class ReportRun(TenantRecord, Base):
    __tablename__ = "report_runs"
    __table_args__ = {"schema": "app"}
    kind: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DocumentIdentity(TenantRecord, Base):
    __tablename__ = "document_identities"
    __table_args__ = {"schema": "app"}
    evidence_id: Mapped[UUID]
    supplier: Mapped[str] = mapped_column(String(160))
    normalized_supplier: Mapped[str] = mapped_column(String(320))
    document_date: Mapped[date]
    total: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    currency: Mapped[str] = mapped_column(String(3))
    version: Mapped[int]
    reason: Mapped[str] = mapped_column(String(1000))
    reviewed_by: Mapped[UUID]


class ExportSnapshot(TenantRecord, Base):
    __tablename__ = "export_snapshots"
    __table_args__ = {"schema": "app"}
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    format: Mapped[str] = mapped_column(String(20))
    created_by: Mapped[UUID]
    business_version: Mapped[int]
    digest: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]
    payload: Mapped[bytes]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrivacyReview(TenantRecord, Base):
    __tablename__ = "privacy_reviews"
    __table_args__ = {"schema": "app"}
    request_id: Mapped[UUID]
    outcome: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(1000))
    policy_reference: Mapped[str] = mapped_column(String(200))
    retained_categories: Mapped[list[str]] = mapped_column(JSONB)
    retain_until: Mapped[date | None]
    legal_hold: Mapped[bool]
    reviewed_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class OpeningCorrection(TenantRecord, Base):
    __tablename__ = "opening_corrections"
    __table_args__ = {"schema": "app"}
    opening_id: Mapped[UUID]
    accounting_date: Mapped[date]
    delta: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    document_id: Mapped[UUID | None]
    loan_movement_id: Mapped[UUID | None]
    journal_id: Mapped[UUID]
    reason: Mapped[str] = mapped_column(String(1000))
    reviewed_by: Mapped[UUID]


class OpeningCashPolicy(TenantRecord, Base):
    __tablename__ = "opening_cash_policies"
    __table_args__ = {"schema": "app"}
    opening_id: Mapped[UUID]
    treatment: Mapped[str] = mapped_column(String(30))
    tax_treatment: Mapped[str] = mapped_column(String(30))
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(1000))
    evidence_id: Mapped[UUID]
    reviewed_by: Mapped[UUID]


class ImportBatch(TenantRecord, Base):
    __tablename__ = "import_batches"
    __table_args__ = {"schema": "app"}
    file_hash: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20))
    cutover_date: Mapped[date]
    source: Mapped[str]
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    expected_trial: Mapped[dict[str, str]] = mapped_column(JSONB)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Evidence(TenantRecord, Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    filename: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    status: Mapped[str] = mapped_column(String(30))
    scan_version: Mapped[str | None] = mapped_column(String(160))
    object_key: Mapped[str] = mapped_column(String(200))
    raw_text: Mapped[str | None]
    extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[UUID]


class EvidenceJob(TenantRecord, Base):
    __tablename__ = "evidence_jobs"
    __table_args__ = {"schema": "app"}
    evidence_id: Mapped[UUID]
    created_by: Mapped[UUID]
    state: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    error_code: Mapped[str | None] = mapped_column(String(50))


class EvidenceNote(TenantRecord, Base):
    __tablename__ = "evidence_notes"
    __table_args__ = {"schema": "app"}
    evidence_id: Mapped[UUID]
    note: Mapped[str] = mapped_column(String(2000))
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class EvidenceLink(TenantRecord, Base):
    __tablename__ = "evidence_links"
    __table_args__ = {"schema": "app"}
    evidence_id: Mapped[UUID]
    resource_kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[UUID]


class Notification(TenantRecord, Base):
    __tablename__ = "notifications"
    __table_args__ = {"schema": "app"}
    recipient_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID | None]
    dedupe_key: Mapped[str] = mapped_column(String(160))
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Preference(TenantRecord, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = {"schema": "app"}
    user_id: Mapped[UUID]
    marketing: Mapped[bool] = mapped_column(default=False)
    reminders: Mapped[bool] = mapped_column(default=True)
    quiet_start: Mapped[int] = mapped_column(default=21)
    quiet_end: Mapped[int] = mapped_column(default=8)
    version: Mapped[int] = mapped_column(default=0)


class CloseTask(TenantRecord, Base):
    __tablename__ = "close_tasks"
    __table_args__ = {"schema": "app"}
    period_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(160))
    assigned_to: Mapped[UUID]
    required: Mapped[bool] = mapped_column(default=True)
    state: Mapped[str] = mapped_column(String(20), default="open")
    prepared_by: Mapped[UUID | None]
    reviewed_by: Mapped[UUID | None]
    evidence_id: Mapped[UUID | None]
    comment: Mapped[str] = mapped_column(String(2000), default="")
    version: Mapped[int] = mapped_column(default=0)


class Consent(TenantRecord, Base):
    __tablename__ = "consents"
    __table_args__ = {"schema": "app"}
    user_id: Mapped[UUID]
    purpose: Mapped[str] = mapped_column(String(50))
    policy_version: Mapped[str] = mapped_column(String(80))
    granted: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PrivacyRequest(TenantRecord, Base):
    __tablename__ = "privacy_requests"
    __table_args__ = {"schema": "app"}
    user_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="awaiting_retention_review")
    reason: Mapped[str] = mapped_column(String(1000))


class TrialEntitlement(TenantRecord, Base):
    __tablename__ = "trial_entitlements"
    __table_args__ = {"schema": "app"}
    status: Mapped[str] = mapped_column(String(20), default="manual_trial")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_row_limit: Mapped[int] = mapped_column(default=500)
    document_byte_limit: Mapped[int] = mapped_column(default=26214400)


class OpeningItem(TenantRecord, Base):
    __tablename__ = "opening_items"
    __table_args__ = {"schema": "app"}
    kind: Mapped[str] = mapped_column(String(20))
    party_id: Mapped[UUID]
    reference: Mapped[str] = mapped_column(String(80))
    original_date: Mapped[date]
    due_date: Mapped[date]
    cutover_date: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    equity_account_id: Mapped[UUID]
    document_id: Mapped[UUID | None]
    loan_movement_id: Mapped[UUID | None]
    reviewed_by: Mapped[UUID]
    review_reason: Mapped[str] = mapped_column(String(1000))


class BeneficiaryChange(TenantRecord, Base):
    __tablename__ = "beneficiary_changes"
    __table_args__ = {"schema": "app"}
    party_id: Mapped[UUID]
    key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    encrypted_details: Mapped[str]
    masked_reference: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="held")
    created_by: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Advance(TenantRecord, Base):
    __tablename__ = "advances"
    __table_args__ = (UniqueConstraint("business_id", "id"), {"schema": "app"})
    party_id: Mapped[UUID]
    direction: Mapped[str] = mapped_column(String(10))
    accounting_date: Mapped[date]
    deposit_account_id: Mapped[UUID]
    bank_account_id: Mapped[UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    journal_id: Mapped[UUID]


class AdvanceApplication(TenantRecord, Base):
    __tablename__ = "advance_applications"
    __table_args__ = {"schema": "app"}
    advance_id: Mapped[UUID]
    document_id: Mapped[UUID]
    accounting_date: Mapped[date]
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    journal_id: Mapped[UUID]
