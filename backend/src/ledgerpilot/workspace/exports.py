"""Bounded private evidence pack with relative paths and a signed hash manifest."""

import hashlib
import html
import io
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.encoders import jsonable_encoder
from sqlalchemy import Select, Text, cast, func, select
from sqlalchemy.orm import Session

from ledgerpilot.accounting.models import Journal
from ledgerpilot.banking.models import BankRule, Statement
from ledgerpilot.config import Settings
from ledgerpilot.documents import storage
from ledgerpilot.identity.dependencies import Principal
from ledgerpilot.reports import service as reports
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.query_limits import bounded_scalars as limited_scalars
from ledgerpilot.shared.telemetry import emit
from ledgerpilot.trade.models import TradeDocument
from ledgerpilot.workspace.models import (
    DocumentIdentity,
    Evidence,
    EvidenceLink,
    EvidenceNote,
    OpeningCashPolicy,
    OpeningCorrection,
    OpeningItem,
)
from ledgerpilot.workspace.service import journal_csv


def bounded_scalars[T](db: Session, query: Select[T]) -> list[T]:
    # Reject oversized JSON/text metadata in SQL before constructing ORM objects.
    rows = query.limit(10001).subquery()
    size = db.scalar(select(func.sum(func.octet_length(cast(rows.table_valued(), Text))))) or 0
    if size > 10 * 1024 * 1024:
        raise DomainError("ERR_QUOTA", "Narrow the pack metadata to at most 10 MiB.", 413)
    return limited_scalars(db, query)


def signing_key(settings: Settings) -> Ed25519PrivateKey:
    if settings.export_signing_private_key is None:
        raise DomainError("ERR_EXPORT_UNAVAILABLE", "Export signing is not configured.", 503)
    return Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(settings.export_signing_private_key.get_secret_value())
    )


def public_key(settings: Settings) -> dict[str, str]:
    key = signing_key(settings).public_key()
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "algorithm": "Ed25519",
        "public_key_hex": raw.hex(),
        "sha256_fingerprint": hashlib.sha256(raw).hexdigest(),
    }


def pack(
    db: Session,
    settings: Settings,
    bid: UUID,
    start: date,
    end: date,
    who: Principal,
) -> bytes:
    from ledgerpilot.workspace.service import save_report

    journal_ids = list(
        bounded_scalars(
            db,
            select(Journal.id)
            .where(
                Journal.business_id == bid,
                Journal.status == "posted",
                Journal.accounting_date >= start,
                Journal.accounting_date <= end,
            )
            .limit(10001),
        )
    )
    if len(journal_ids) > 10000:
        raise DomainError("ERR_QUOTA", "Narrow the evidence pack to at most 10,000 journals.", 413)
    documents = list(
        bounded_scalars(
            db,
            select(TradeDocument.id).where(
                TradeDocument.business_id == bid, TradeDocument.journal_id.in_(journal_ids)
            ),
        )
    )
    links = list(
        bounded_scalars(
            db,
            select(EvidenceLink.evidence_id).where(
                EvidenceLink.business_id == bid,
                EvidenceLink.resource_id.in_([*journal_ids, *documents]),
            ),
        )
    )
    statements = list(
        bounded_scalars(
            db,
            select(Statement).where(
                Statement.business_id == bid, Statement.as_of >= start, Statement.as_of <= end
            ),
        )
    )
    links += [s.evidence_id for s in statements]
    evidence = list(
        bounded_scalars(
            db, select(Evidence).where(Evidence.business_id == bid, Evidence.id.in_(set(links)))
        )
    )
    if len(evidence) > 20 or sum(e.size for e in evidence) > 5 * 1024 * 1024:
        raise DomainError(
            "ERR_QUOTA", "The synchronous pilot pack supports 20 files / 5 MiB. Narrow dates.", 413
        )
    if any(e.status not in {"clean", "unsupported", "extracted"} for e in evidence):
        raise DomainError(
            "ERR_EVIDENCE_QUARANTINED", "Selected evidence has not passed scanning.", 409
        )

    class BudgetFiles(dict[str, bytes]):
        size = 0

        def __setitem__(self, name: str, body: bytes) -> None:
            next_size = self.size - len(self.get(name, b"")) + len(body)
            if next_size > 10 * 1024 * 1024:
                raise DomainError("ERR_QUOTA", "Narrow the pack to at most 10 MiB.", 413)
            super().__setitem__(name, body)
            self.size = next_size

    files = BudgetFiles()
    files["journals.csv"] = journal_csv(db, bid, start, end).encode("utf-8")

    def add_json(name: str, value: Any) -> None:
        output = io.BytesIO()
        for chunk in json.JSONEncoder(sort_keys=True, indent=2).iterencode(jsonable_encoder(value)):
            encoded = chunk.encode()
            if files.size + output.tell() + len(encoded) > 10 * 1024 * 1024:
                raise DomainError("ERR_QUOTA", "Narrow the pack to at most 10 MiB.", 413)
            output.write(encoded)
        files[name] = output.getvalue()

    add_json(
        "opening-balances.json",
        [
            {
                **{
                    k: getattr(r, k)
                    for k in (
                        "id",
                        "kind",
                        "party_id",
                        "reference",
                        "original_date",
                        "due_date",
                        "cutover_date",
                        "equity_account_id",
                        "document_id",
                        "loan_movement_id",
                        "reviewed_by",
                        "review_reason",
                    )
                },
                "amount": str(r.amount),
            }
            for r in bounded_scalars(
                db,
                select(OpeningItem)
                .where(OpeningItem.business_id == bid, OpeningItem.cutover_date <= end)
                .order_by(OpeningItem.id),
            )
        ],
    )

    add_json(
        "opening-corrections.json",
        [
            {
                **{
                    k: getattr(r, k)
                    for k in (
                        "id",
                        "opening_id",
                        "accounting_date",
                        "document_id",
                        "loan_movement_id",
                        "journal_id",
                        "reason",
                        "reviewed_by",
                    )
                },
                "delta": str(r.delta),
            }
            for r in bounded_scalars(
                db,
                select(OpeningCorrection).where(
                    OpeningCorrection.business_id == bid, OpeningCorrection.accounting_date <= end
                ),
            )
        ],
    )
    add_json(
        "opening-cash-policies.json",
        [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "opening_id",
                    "treatment",
                    "tax_treatment",
                    "components",
                    "reason",
                    "evidence_id",
                    "reviewed_by",
                )
            }
            for r in bounded_scalars(
                db, select(OpeningCashPolicy).where(OpeningCashPolicy.business_id == bid)
            )
        ],
    )
    add_json(
        "reports/profit-loss.json",
        save_report(db, who, bid, reports.financial(db, bid, start, end, "accrual", "profit-loss")),
    )
    add_json(
        "reports/balance-sheet.json",
        save_report(
            db, who, bid, reports.financial(db, bid, start, end, "accrual", "balance-sheet")
        ),
    )
    add_json("reports/controls.json", save_report(db, who, bid, reports.controls(db, bid, end)))
    add_json(
        "reports/tax-support.json",
        save_report(db, who, bid, reports.tax_support(db, bid, start, end)),
    )
    add_json(
        "statements.json",
        [
            {
                "id": str(s.id),
                "as_of": s.as_of,
                "account_id": str(s.account_id),
                "closing_balance": str(s.closing_balance),
                "ledger_balance": str(s.ledger_balance),
                "outstanding": s.outstanding,
                "reviewer_id": str(s.reviewer_id),
                "evidence_id": str(s.evidence_id),
            }
            for s in statements
        ],
    )
    add_json(
        "posting-provenance.json",
        [
            {
                k: getattr(j, k)
                for k in (
                    "id",
                    "accounting_date",
                    "source_kind",
                    "source_id",
                    "approved_by",
                    "posted_at",
                    "period_id",
                    "policy_id",
                    "operation_key",
                    "book",
                )
            }
            for j in bounded_scalars(
                db, select(Journal).where(Journal.business_id == bid, Journal.id.in_(journal_ids))
            )
        ],
    )
    add_json(
        "rules.json",
        [
            {
                k: getattr(r, k)
                for k in (
                    "id",
                    "name",
                    "contains",
                    "account_id",
                    "version",
                    "active",
                    "history",
                )
            }
            for r in bounded_scalars(
                db, select(BankRule).where(BankRule.business_id == bid).limit(201)
            )
        ],
    )
    notes = list(
        bounded_scalars(
            db,
            select(EvidenceNote)
            .where(
                EvidenceNote.business_id == bid,
                EvidenceNote.evidence_id.in_([e.id for e in evidence]),
            )
            .limit(201),
        )
    )
    if len(notes) > 200:
        raise DomainError("ERR_QUOTA", "Narrow the pack to at most 200 evidence annotations.", 413)
    add_json(
        "evidence-notes.json",
        [
            {
                k: getattr(n, k)
                for k in (
                    "id",
                    "evidence_id",
                    "note",
                    "fields",
                    "created_by",
                    "created_at",
                )
            }
            for n in notes
        ],
    )
    add_json(
        "evidence-identities.json",
        [
            {
                **{
                    k: getattr(r, k)
                    for k in (
                        "id",
                        "evidence_id",
                        "supplier",
                        "normalized_supplier",
                        "document_date",
                        "currency",
                        "version",
                        "reason",
                        "reviewed_by",
                    )
                },
                "total": str(r.total),
            }
            for r in bounded_scalars(
                db,
                select(DocumentIdentity)
                .where(
                    DocumentIdentity.business_id == bid,
                    DocumentIdentity.evidence_id.in_([e.id for e in evidence]),
                )
                .order_by(DocumentIdentity.evidence_id, DocumentIdentity.version),
            )
        ],
    )
    index = []
    for item in evidence:
        # Generated filenames keep original untrusted names out of archive paths.
        name = f"evidence/{item.id}{Path(item.filename).suffix.lower()}"
        try:
            files[name] = storage.get(settings, item.object_key, item.content_hash)
        except DomainError as error:
            emit(
                "export.evidence_failed",
                level=logging.ERROR,
                error=error,
                code=error.code,
                resource_id=item.id,
                business_id=bid,
            )
            raise
        index.append(f'<li><a href="{name}">{html.escape(item.filename)}</a></li>')
    files["index.html"] = (
        '<!doctype html><meta charset="utf-8"><title>LedgerPilot evidence pack</title>'
        "<h1>LedgerPilot evidence pack</h1>"
        "<p>Hashes evidence integrity, not truth of source content.</p>"
        "<p>Compare the public-key fingerprint with the authenticated verification-key endpoint "
        "before trusting the signature. Local report JSON and journals CSV are included.</p><ul>"
        + "".join(index)
        + "</ul>"
    ).encode()
    add_json(
        "manifest.json",
        {
            "business_id": str(bid),
            "start": start,
            "end": end,
            "files": {
                name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
                for name, body in sorted(files.items())
            },
            "verification_key": public_key(settings),
        },
    )
    files["manifest.sig"] = signing_key(settings).sign(files["manifest.json"])
    if sum(len(body) for body in files.values()) > 10 * 1024 * 1024:
        raise DomainError(
            "ERR_QUOTA", "Pilot evidence packs are capped at 10 MiB before compression.", 413
        )
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return output.getvalue()
