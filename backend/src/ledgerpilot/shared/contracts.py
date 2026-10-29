"""Explicit HTTP response contracts, including intentionally polymorphic JSON payloads.

The factories only reduce schema boilerplate. Every field is allowlisted here;
ORM entities and arbitrary object attributes are never serialized wholesale.
"""

from datetime import date, datetime
from types import GenericAlias
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, create_model

from ledgerpilot.accounting.schemas import LineCreate
from ledgerpilot.trade.schemas import DocumentCreate

JSON = dict[str, JsonValue]


class ReadSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


def obj(schema_name: str, /, **fields: Any) -> Any:
    definitions: dict[str, Any] = {
        k: v if isinstance(v, tuple) else (v, ...) for k, v in fields.items()
    }
    return create_model(schema_name, __base__=ReadSchema, **definitions)


def many(annotation: Any) -> Any:
    return GenericAlias(list, annotation)


def opt(annotation: Any) -> Any:
    return (annotation | None, None)


def page(name: str, row: Any) -> Any:
    return obj(name, items=many(row), next_cursor=str | None)


ID = obj("ActionId", id=UUID)
Status = obj("ActionStatus", status=str)
IdVersion = obj("IdVersion", id=UUID, version=int)
IdState = obj("IdState", id=UUID, version=int, state=str)
IdStatus = obj("IdStatus", id=UUID, version=int, status=str)
IdActive = obj("IdActive", id=UUID, version=int, active=bool)
Posted = obj("PostedSource", id=UUID, journal_id=UUID)
Context = dict(
    report_id=UUID, as_of=date, basis=str, currency=str, timezone=str, book=str, policy=str
)
FinancialRow = obj("FinancialRow", account_id=UUID, code=str, name=str, kind=str, amount=str)
Financial = obj(
    "FinancialReport",
    **Context,
    start_date=date,
    kind=str,
    items=many(FinancialRow),
    income=str,
    expense=str,
    net_profit=str,
    opening_cash_policy_ids=many(UUID),
    retained_earnings=opt(str),
    balanced=opt(bool),
)
AgingRow = obj(
    "AgingRow",
    document_id=UUID,
    party_id=UUID,
    number=str,
    due_date=date,
    disputed=bool,
    bucket=str,
    outstanding=str,
)
Aging = obj("AgingReport", **Context, items=many(AgingRow), total=str)
ControlRow = obj(
    "ControlRow",
    account_id=UUID,
    control=str,
    ledger_balance=str,
    subledger_balance=str,
    difference=str,
    balanced=bool,
)
Controls = obj("ControlReport", **Context, items=many(ControlRow), balanced=bool)
TaxRow = obj(
    "TaxSupportRow",
    account_id=UUID,
    name=str,
    kind=str,
    opening_debit_minus_credit=str,
    closing_debit_minus_credit=str,
    source_balance=str,
    difference=str,
    source_ids=many(str),
)
Tax = obj(
    "TaxSupportReport",
    **Context,
    kind=str,
    start_date=date,
    items=many(TaxRow),
    statutory_filing=bool,
    source_baseline_id=UUID | None,
    balanced=bool,
)
Week = obj(
    "ForecastWeek",
    week=int,
    start_date=date,
    end_date=date,
    receipts=str,
    payments=str,
    closing_cash=str,
    shortfall=bool,
    document_ids=many(UUID),
)
Forecast = obj(
    "ForecastReport", **Context, weeks=many(Week), confidence=str, bands=None, assumptions=many(str)
)
Trend = obj("MonthlyTrend", start_date=date, end_date=date, income=str, expense=str, net_profit=str)
Dashboard = create_model(
    "DashboardReport",
    __base__=Financial,
    trend=(many(Trend), ...),
    cash_balance=(str, ...),
    cash_account_ids=(many(UUID), ...),
    receivables=(Aging, ...),
    payables=(Aging, ...),
    controls=(Controls, ...),
)
Readiness = obj(
    "BooksReadiness",
    **Context,
    kind=str,
    unreviewed_bank_rows=int,
    mandatory_tasks_pending=int,
    controls=Controls,
    tax_controls=Tax,
    ready=bool,
    checks=many(str),
    limitations=many(str),
)
PartyEvent = obj(
    "PartyStatementEvent",
    id=UUID,
    accounting_date=date,
    kind=str,
    number=str,
    journal_id=UUID,
    amount=str,
    effect=str,
    interest=str,
    fees=str,
    running_balance=str,
)
PartyStatement = obj(
    "PartyStatementReport",
    **Context,
    kind=str,
    party_id=UUID,
    party_name=str,
    role=str,
    items=many(PartyEvent),
    next_cursor=str | None,
    total=str,
    balance_definition=str,
)
Report = Dashboard | Financial | Aging | Controls | Tax | Forecast | Readiness | PartyStatement
Rule = obj(
    "RuleSnapshot",
    id=opt(UUID),
    name=str,
    contains=str,
    account_id=UUID,
    priority=int,
    version=int,
    active=bool,
)
RuleSample = obj("RuleSample", id=UUID, version=int, amount=str)
RulePreview = obj(
    "RulePreview",
    rule_id=UUID,
    version=int,
    account_id=UUID,
    contains=str,
    rows=many(RuleSample),
    preview_hash=str,
)
Candidate = obj("RuleCandidate", rule_id=UUID, rule_version=int, account_id=UUID, reason=str)
Proposal = obj(
    "BankProposal",
    row_id=UUID,
    mode=str,
    model_version=None,
    confidence=None,
    requires_approval=bool,
    status=str,
    candidates=many(Candidate),
    posted=bool,
)
CSVRow = obj(
    "CSVPreviewRow",
    position=int,
    accounting_date=date,
    description=str,
    amount=str,
    raw=dict[str, str],
    external_id=str | None,
)
CSVError = obj("ImportRowError", row=int, code=str, message=str)
CSVPreview = obj(
    "BankCSVPreview", file_hash=str, rows=many(CSVRow), errors=many(CSVError), can_import=bool
)
BankPreview = obj(
    "BankImpactPreview",
    posted=bool,
    currency=str,
    timezone=str,
    business_version=int,
    row_version=int,
    action=str,
    lines=many(LineCreate),
    period_open=opt(bool),
    matched_journal_ids=opt(many(UUID)),
)
CorrectionResult = obj(
    "BankCorrectionResult",
    id=UUID,
    journal_id=UUID,
    original_row_id=UUID,
    bank_movement_changed=bool,
)
CorrectionRow = obj(
    "BankCorrectionRead",
    id=UUID,
    row_id=UUID,
    accounting_date=date,
    journal_id=UUID,
    reason=str,
    splits=many(JSON),
    created_by=UUID,
)
RuleSuggestion = obj(
    "CorrectionRuleSuggestion",
    descriptor=str,
    suggested_contains=str,
    account_id=UUID,
    correction_ids=many(UUID),
    sample_count=int,
    requires_preview_and_approval=bool,
    active=bool,
)
RuleSuggestions = obj(
    "CorrectionRuleSuggestions",
    items=many(RuleSuggestion),
    minimum_distinct_transactions=int,
    automatic_activation=bool,
)
EvidenceRead = obj(
    "EvidenceRead",
    id=UUID,
    filename=str,
    content_hash=str,
    media_type=str,
    size=int,
    status=str,
    scan_version=str | None,
    raw_text=str | None,
    extraction=JSON,
)
EvidenceSummary = obj(
    "EvidenceSummary",
    id=UUID,
    filename=str,
    content_hash=str,
    media_type=str,
    size=int,
    status=str,
    scan_version=opt(str),
    extraction=JSON,
)
EvidenceUpload = obj(
    "EvidenceUpload",
    id=UUID,
    content_hash=str,
    scan_status=str,
    job_id=UUID,
    extraction=str | JSON,
    duplicate_candidates=many(UUID),
    manual_review_available=bool,
    posted=bool,
)
FieldRegion = obj(
    "ReviewedFieldRegion",
    name=str,
    value=str,
    value_type=str,
    page=int,
    left=float,
    top=float,
    right=float,
    bottom=float,
    confidence=None,
    source=str,
    model_version=None,
)
NoteResult = obj("EvidenceAnnotationResult", id=UUID, fields=many(FieldRegion), note=str)
NoteRead = obj(
    "EvidenceAnnotation",
    id=UUID,
    note=str,
    fields=many(FieldRegion),
    created_by=UUID,
    created_at=datetime,
)
Capability = obj(
    "EvidenceCapabilities",
    ocr_configured=bool,
    automatic_types=many(str),
    ocr_model_language=str,
    pdf_text_first=bool,
    max_pages=int,
    max_image_pixels=int,
    max_bytes=int,
    confidence_is_calibrated=bool,
    semantic_assignment=str,
    human_review_required=bool,
    automatic_posting=bool,
)
DuplicateCandidate = obj("DuplicateCandidate", evidence_id=UUID, reasons=many(str))
Duplicates = obj(
    "DuplicateCandidates",
    items=many(DuplicateCandidate),
    requires_review=bool,
    automatic_merge=bool,
    identity_available=bool,
    abstention=str | None,
)
Draft = obj(
    "SourceDraftRead",
    id=UUID,
    payload=DocumentCreate,
    status=str,
    version=int,
    document_id=UUID | None,
    prepared_by=UUID,
)
Operation = obj(
    "OperationRead", id=UUID, action=str, journal_id=UUID | None, result=JSON, created_at=datetime
)
Advance = obj(
    "AdvanceRead",
    id=UUID,
    party_id=UUID,
    direction=str,
    accounting_date=date,
    amount=str,
    unapplied=str,
    deposit_account_id=UUID,
    journal_id=UUID,
)
Movement = obj(
    "LoanMovementRead",
    id=UUID,
    accounting_date=date,
    kind=str,
    principal=str,
    interest=str,
    fees=str,
    journal_id=UUID,
)
Opening = obj(
    "OpeningBalanceRead",
    id=UUID,
    kind=str,
    party_id=UUID,
    reference=str,
    original_date=date,
    due_date=date,
    cutover_date=date,
    equity_account_id=UUID,
    document_id=UUID | None,
    loan_movement_id=UUID | None,
    reviewed_by=UUID,
    review_reason=str,
    amount=str,
)
OpeningChange = obj(
    "OpeningCorrectionRead",
    id=UUID,
    accounting_date=date,
    document_id=UUID | None,
    loan_movement_id=UUID | None,
    journal_id=UUID,
    reason=str,
    reviewed_by=UUID,
    delta=str,
)
CashPolicy = obj(
    "OpeningCashPolicyRead",
    id=UUID,
    treatment=str,
    tax_treatment=str,
    components=many(JSON),
    reason=str,
    evidence_id=UUID,
    reviewed_by=UUID,
)
Snapshot = obj(
    "ExportSnapshotRead",
    id=UUID,
    format=str,
    size=int,
    sha256=str,
    expires_at=datetime,
    business_version=int,
    state=str,
    range_chunk_limit=int,
)
PrivacyRequest = obj("PrivacyRequestRead", id=UUID, kind=str, status=str, reason=str)
Consent = obj(
    "ConsentRead", id=UUID, purpose=str, policy_version=str, granted=bool, created_at=datetime
)
PrivacyDecision = obj(
    "PrivacyDecisionRead",
    id=UUID,
    outcome=str,
    reason=str,
    policy_reference=str,
    retained_categories=many(str),
    retain_until=date | None,
    legal_hold=bool,
    reviewed_by=UUID,
    created_at=datetime,
)
Job = obj(
    "EvidenceJobRead",
    id=UUID,
    evidence_id=UUID,
    created_by=UUID,
    state=str,
    attempts=int,
    available_at=datetime,
    error_code=str | None,
)
Task = obj(
    "CloseTaskRead",
    id=UUID,
    period_id=UUID,
    name=str,
    assigned_to=UUID,
    required=bool,
    state=str,
    prepared_by=UUID | None,
    reviewed_by=UUID | None,
    evidence_id=UUID | None,
    comment=str,
    version=int,
)
Preference = obj(
    "NotificationPreferences",
    version=int,
    marketing=bool,
    reminders=bool,
    quiet_start=int,
    quiet_end=int,
)
Notification = obj(
    "NotificationRead", id=UUID, kind=str, resource_id=UUID | None, read=bool, created_at=datetime
)
Audit = obj(
    "AuditEventRead",
    id=UUID,
    user_id=UUID | None,
    action=str,
    resource_id=UUID | None,
    occurred_at=datetime,
    details=JSON,
)
ImportPreview = obj(
    "MigrationPreview",
    preview_hash=str,
    commands=many(JSON),
    errors=many(CSVError),
    can_apply=bool,
    posted=bool,
    cutover_date=date,
    business_version=int,
    required_approval_role=str | None,
    policy=str,
)
ImportApply = obj("MigrationResult", id=UUID, applied=int, results=many(JSON), atomic=bool)
ImportBatch = obj(
    "ImportBatchRead",
    id=UUID,
    file_hash=str,
    mode=str,
    cutover_date=date,
    results=many(JSON),
    expected_trial=dict[str, str],
    created_by=UUID,
)
CompareRow = obj(
    "ParallelComparisonRow", account_id=UUID, code=str, expected=str, actual=str, difference=str
)
Comparison = obj(
    "ImportComparison",
    batch_id=UUID,
    as_of=opt(date),
    items=opt(many(CompareRow)),
    balanced=opt(bool),
    controls=opt(Controls),
    basis=opt(str),
    comparison_available=opt(bool),
    reason=opt(str),
)
Member = obj(
    "MembershipRead",
    user_id=UUID,
    role=str,
    active=bool,
    can_post=bool,
    can_invite=bool,
    version=int,
)
responses: dict[str, Any] = {
    "identity.csrf": obj("CSRFRead", csrf_token=str),
    "identity.me": obj("IdentityRead", id=UUID, email=str, verified=bool, mfa=bool),
    "identity.enroll": obj("MFAEnrollment", secret=str, provisioning_uri=str),
    "identity.confirm": obj("MFARecoveryCodes", recovery_codes=many(str)),
    "tenancy.accept": obj("AcceptedInvitation", business_id=UUID),
    "tenancy.change_policy": obj("PolicyVersion", policy_version=int),
    "tenancy.policies": obj(
        "PolicyHistory",
        items=many(
            obj("PolicyRead", version=int, effective_date=date, basis=str, jurisdiction=str)
        ),
        next_version=int | None,
    ),
    "accounting.create_draft": IdStatus,
    "accounting.update_draft": IdStatus,
    "accounting.update_account": IdActive,
    "accounting.lock_period": obj("PeriodLockResult", id=UUID, locked=bool, version=int),
    "accounting.tax_remittance": obj(
        "TaxRemittanceResult",
        id=UUID,
        journal_id=UUID,
        amount=str,
        tax_account_id=UUID,
        accounting_date=date,
    ),
    "accounting.ledger": obj(
        "GeneralLedgerRead",
        currency=str,
        timezone=str,
        start=date,
        end=date,
        report_type=str,
        items=many(
            obj(
                "LedgerLine",
                id=UUID,
                journal_id=UUID,
                date=date,
                account_id=UUID,
                debit=str,
                credit=str,
            )
        ),
        next_cursor=str | None,
    ),
    "banking.correction_suggestions": RuleSuggestions,
    "banking.reclassify": CorrectionResult,
    "banking.reclassifications": page("BankCorrections", CorrectionRow),
    "banking.disposition_preview": BankPreview,
    "banking.matches": obj(
        "BankMatches",
        items=many(
            obj(
                "BankJournalMatch",
                id=UUID,
                accounting_date=date,
                memo=str,
                source_kind=str,
                source_id=UUID | None,
            )
        ),
    ),
    "banking.preview": CSVPreview,
    "banking.rule_create": IdActive,
    "banking.rules": obj("BankRules", items=many(Rule)),
    "banking.preview_rule": RulePreview,
    "banking.update_rule": Rule,
    "banking.activate_rule": Rule,
    "banking.rollback_rule": Rule,
    "banking.proposal": Proposal,
    "banking.sign_off": obj(
        "StatementSignoff",
        id=UUID,
        ledger_balance=str,
        statement_balance=str,
        outstanding=str,
        unexplained_difference=str,
    ),
    "documents.identity_review": obj(
        "ReviewedDocumentIdentity",
        id=UUID,
        evidence_id=UUID,
        version=int,
        normalized_supplier=str,
        automatic_merge=bool,
    ),
    "documents.duplicate_candidates": Duplicates,
    "documents.capabilities": Capability,
    "documents.evidence_detail": EvidenceRead,
    "documents.upload": EvidenceUpload,
    "documents.list_evidence": page("EvidenceList", EvidenceSummary),
    "documents.note": NoteResult,
    "documents.notes": page("EvidenceAnnotations", NoteRead),
    "documents.link": ID,
    "reports.profit_loss": Financial,
    "reports.balance_sheet": Financial,
    "reports.aging": Aging,
    "reports.controls": Controls,
    "reports.tax_support": Tax,
    "reports.forecast": Forecast,
    "reports.dashboard": Dashboard,
    "trade.party_statement": PartyStatement,
    "trade.preview_document": obj(
        "DocumentImpact",
        currency=str,
        timezone=str,
        business_version=int,
        total=str,
        tax=str,
        lines=many(LineCreate),
        posted=bool,
    ),
    "trade.source_draft_list": page("SourceDrafts", Draft),
    "trade.operation": Operation,
    "trade.advances_list": page("Advances", Advance),
    "trade.movements": page("LoanMovements", Movement),
    "trade.advance_create": Posted,
    "trade.advance_apply": Posted,
    "trade.dispute": IdVersion,
    "trade.loan": ID,
    "workspace.opening_correction": obj(
        "OpeningCorrectionResult",
        id=UUID,
        correction_id=UUID,
        journal_id=UUID,
        opening_id=UUID,
        delta=str,
    ),
    "workspace.opening_cash_policy": obj(
        "OpeningCashApproval", id=UUID, opening_id=UUID, treatment=str
    ),
    "workspace.opening_history": obj(
        "OpeningHistory", corrections=many(OpeningChange), cash_policy=CashPolicy | None
    ),
    "workspace.export_snapshot": Snapshot,
    "workspace.snapshot_status": Snapshot,
    "workspace.privacy_queue": page(
        "PrivacyReviewQueue", obj("PrivacyQueueEntry", id=UUID, user_id=UUID, kind=str, reason=str)
    ),
    "workspace.review_privacy": obj(
        "PrivacyReviewResult",
        id=UUID,
        request_id=UUID,
        status=str,
        deleted=bool,
        retained_categories=many(str),
        policy_reference=str,
        export_path=str | None,
        next_action=str,
    ),
    "workspace.privacy_history": obj(
        "PrivacyHistory",
        id=UUID,
        kind=str,
        requester_id=UUID,
        status=str,
        deleted=bool,
        decisions=many(PrivacyDecision),
    ),
    "workspace.opening_balances": page("OpeningBalances", Opening),
    "workspace.privacy_export": obj(
        "PersonalExport",
        scope=str,
        user=obj("ExportedIdentity", id=UUID, email=str, created_at=datetime),
        membership=obj("ExportedMembership", business_id=UUID, role=str, active=bool),
        consents=many(Consent),
        privacy_requests=many(PrivacyRequest),
        financial_records=str,
        deletion_status=str,
    ),
    "workspace.job_replay": obj("ReplayedJob", id=UUID, state=str),
    "workspace.beneficiary_request": obj(
        "HeldBeneficiaryChange", id=UUID, status=str, effective=bool
    ),
    "workspace.beneficiary_changes": page(
        "BeneficiaryChanges",
        obj(
            "BeneficiaryChangeRead",
            id=UUID,
            party_id=UUID,
            status=str,
            masked_reference=str,
            reason=str,
            created_by=UUID,
            created_at=datetime,
        ),
    ),
    "workspace.privacy_requests": page("PrivacyRequests", PrivacyRequest),
    "workspace.consents": page("Consents", Consent),
    "workspace.usage": obj(
        "UsageRead",
        profile=str,
        documents=int,
        document_limit=int,
        evidence_bytes=int,
        evidence_byte_limit=int,
        operations=int,
        operation_limit=int,
        jobs=dict[str, int],
        user_per_business_per_minute=int,
        business_per_minute=int,
        automatic_paid_upgrade=bool,
    ),
    "workspace.import_preview": ImportPreview,
    "workspace.import_apply": ImportApply,
    "workspace.imports_list": page("ImportBatches", ImportBatch),
    "workspace.import_comparison": Comparison,
    "workspace.export_key": obj(
        "ExportVerificationKey", algorithm=str, public_key_hex=str, sha256_fingerprint=str
    ),
    "workspace.jobs": page("EvidenceJobs", Job),
    "workspace.members": obj("Memberships", items=many(Member)),
    "workspace.update_member": obj("UpdatedMembership", user_id=UUID, version=int),
    "workspace.report": Report,
    "workspace.answer": obj(
        "ReportAnswer",
        report_id=UUID,
        metric=str,
        value=str | bool,
        currency=str | None,
        basis=str | None,
        mode=str,
        model_version=None,
        actions_executed=bool,
    ),
    "workspace.notifications": page("Notifications", Notification),
    "workspace.notification_read": obj("NotificationMarkedRead", read=bool),
    "workspace.preferences": Preference,
    "workspace.preference_update": Preference,
    "workspace.review_reminders": obj(
        "ReminderReview", created=int, channel=str, external_messages_sent=int
    ),
    "workspace.create_task": IdState,
    "workspace.task_update": IdState,
    "workspace.tasks": page("CloseTasks", Task),
    "workspace.readiness": Readiness,
    "workspace.audits": page("AuditEvents", Audit),
    "workspace.search": page(
        "SearchResults", obj("SearchHit", id=UUID, kind=str, number=str, party_id=UUID)
    ),
    "workspace.consent": obj("RecordedConsent", id=UUID, purpose=str, granted=bool),
    "workspace.privacy": obj(
        "PrivacyRequestResult",
        id=UUID,
        status=str,
        retained_categories=many(str),
        retention_basis=str,
        deleted=bool,
    ),
    "workspace.entitlement": obj(
        "EntitlementRead",
        status=str,
        live_charges=bool,
        expires_at=datetime | None,
        write_allowed=bool,
        bank_import_row_limit=int,
        document_byte_limit=int,
    ),
}
for name in ("register", "verification_request", "reset_request"):
    responses["identity." + name] = obj("AuthDeliveryResult", message=str)
for name in ("verify", "reset", "login", "refresh", "logout", "logout_all"):
    responses["identity." + name] = Status
responses["tenancy.invite"] = Status
for name in ("source_draft_create", "source_draft_edit", "source_draft_issue", "source_draft_void"):
    responses["trade." + name] = Draft
responses["trade.source_draft_issue"] = obj("IssuedSourceDraft", id=UUID, draft_id=UUID, status=str)
