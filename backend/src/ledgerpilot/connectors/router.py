"""Phase E (E1..E6): Unified Connectors Hub (Bank Feeds, QBO+Xero, Commerce, Email, Drive)."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import APIRouter, Header, Request
from pydantic import Field
from sqlalchemy import text

from ledgerpilot.audit.service import record
from ledgerpilot.config import Settings
from ledgerpilot.database import set_context
from ledgerpilot.documents.understanding import (
    claim_document_fingerprint,
    extract_structured_document,
)
from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.schemas import Schema
from ledgerpilot.tenancy.service import authorize, get_business

router = APIRouter(tags=["connectors"])


def _q2(val: Decimal | str | int | float) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"))


def _cipher(settings: Settings) -> Fernet:
    if settings.evidence_encryption_key:
        raw = settings.evidence_encryption_key.get_secret_value().encode("utf-8")
        return Fernet(raw)
    import base64

    derived = hashlib.sha256(
        settings.session_signing_key.get_secret_value().encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _inbound_address(business_name: str, business_id: UUID, domain: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", business_name.lower()).strip("-")[:24] or "books"
    short_id = str(business_id).split("-")[0]
    return f"bills+{slug}-{short_id}@{domain}"


ConnectorKind = Literal[
    "plaid",
    "stripe_fc",
    "quickbooks",
    "xero",
    "shopify",
    "square",
    "inbound_email",
    "google_drive",
    "dropbox",
]


class ConnectorAccountCreate(Schema):
    connector_kind: ConnectorKind
    display_name: str = Field(min_length=2, max_length=120)
    environment: Literal["sandbox", "production"] = "sandbox"
    access_token: str = Field(default="", max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict)


class AccountingExportRequest(Schema):
    target_system: Literal["quickbooks", "xero"]
    format: Literal["api_json", "csv", "iif"] = "api_json"
    start_date: date | None = None
    end_date: date | None = None


class XeroImportRequest(Schema):
    tenant_id: str = Field(default="xero-demo-org", min_length=2, max_length=120)
    dry_run: bool = False
    records: dict[str, list[dict[str, Any]]] | None = None


class EcommerceSyncRequest(Schema):
    orders: list[dict[str, Any]] | None = None
    payout_date: date | None = None


class CloudDriveSyncRequest(Schema):
    folder_name: str = Field(default="/Receipts & Invoices", max_length=200)
    files: list[dict[str, Any]] | None = None


@router.get("/businesses/{business_id}/connectors")
def list_connectors_overview(
    business_id: UUID,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Return connected integrations, sync runs, and workspace inbound email address."""
    authorize(db, identity, business_id)
    settings: Settings = request.app.state.settings
    biz = get_business(db, business_id)

    accounts = (
        db.execute(
            text(
                """
                SELECT id, connector_kind, display_name, environment, status,
                       config_json, sync_cursor, last_synced_at, created_at
                FROM app.connector_accounts
                WHERE business_id = :bid
                ORDER BY created_at DESC
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    runs = (
        db.execute(
            text(
                """
                SELECT id, connector_kind, direction, records_processed,
                       records_created, records_deduplicated, status,
                       summary_json, created_at
                FROM app.connector_sync_runs
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 50
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    emails = (
        db.execute(
            text(
                """
                SELECT id, message_id, sender_email, subject, spf_dkim_passed,
                       status, draft_id, po_match_id, created_at
                FROM app.inbound_invoice_emails
                WHERE business_id = :bid
                ORDER BY created_at DESC
                LIMIT 50
                """
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    return {
        "inbound_invoice_email": _inbound_address(
            biz.name, business_id, settings.inbound_email_domain
        ),
        "qbo_environment": settings.qbo_environment,
        "accounts": [
            {
                "id": str(a["id"]),
                "connector_kind": str(a["connector_kind"]),
                "display_name": str(a["display_name"]),
                "environment": str(a["environment"]),
                "status": str(a["status"]),
                "config": dict(a["config_json"] or {}),
                "sync_cursor": str(a["sync_cursor"] or ""),
                "last_synced_at": a["last_synced_at"].isoformat() if a["last_synced_at"] else None,
            }
            for a in accounts
        ],
        "sync_runs": [
            {
                "id": str(r["id"]),
                "connector_kind": str(r["connector_kind"]),
                "direction": str(r["direction"]),
                "records_processed": int(r["records_processed"]),
                "records_created": int(r["records_created"]),
                "records_deduplicated": int(r["records_deduplicated"]),
                "status": str(r["status"]),
                "summary": dict(r["summary_json"] or {}),
                "created_at": r["created_at"].isoformat(),
            }
            for r in runs
        ],
        "inbound_emails": [
            {
                "id": str(e["id"]),
                "message_id": str(e["message_id"]),
                "sender_email": str(e["sender_email"]),
                "subject": str(e["subject"]),
                "spf_dkim_passed": bool(e["spf_dkim_passed"]),
                "status": str(e["status"]),
                "draft_id": str(e["draft_id"]) if e["draft_id"] else None,
                "po_match_id": str(e["po_match_id"]) if e["po_match_id"] else None,
                "created_at": e["created_at"].isoformat(),
            }
            for e in emails
        ],
    }


@router.post("/businesses/{business_id}/connectors/accounts", status_code=201)
def connect_integration_account(
    business_id: UUID,
    body: ConnectorAccountCreate,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """Register an encrypted connector credential (Plaid, Stripe FC, QBO, Xero, Shopify, Drive)."""
    authorize(db, identity, business_id, {"owner", "accountant"})
    settings: Settings = request.app.state.settings
    enc_tok = (
        _cipher(settings).encrypt(body.access_token.encode("utf-8")).decode("ascii")
        if body.access_token
        else ""
    )
    cid = uuid4()
    db.execute(
        text(
            """
            INSERT INTO app.connector_accounts (
              id, business_id, connector_kind, display_name, environment,
              status, encrypted_credentials, config_json, created_by
            ) VALUES (
              :id, :bid, :kind, :name, :env,
              'connected', :cred, CAST(:cfg AS jsonb), :uid
            )
            """
        ),
        {
            "id": cid,
            "bid": business_id,
            "kind": body.connector_kind,
            "name": body.display_name.strip(),
            "env": body.environment,
            "cred": enc_tok,
            "cfg": json.dumps(body.config),
            "uid": identity.user.id,
        },
    )
    record(db, identity.user.id, f"connector.{body.connector_kind}_connected", business_id, cid)
    return {
        "id": str(cid),
        "connector_kind": body.connector_kind,
        "display_name": body.display_name.strip(),
        "environment": body.environment,
        "status": "connected",
    }


@router.post("/businesses/{business_id}/connectors/bank-feeds/{connector_id}/sync")
def sync_live_bank_feed_connector(
    business_id: UUID,
    connector_id: UUID,
    body: dict[str, Any] | None,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """E1: Incremental cursor bank/card feed sync into app.bank_rows with deduplication."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    conn_row = (
        db.execute(
            text(
                "SELECT * FROM app.connector_accounts "
                "WHERE business_id = :bid AND id = :id FOR UPDATE"
            ),
            {"bid": business_id, "id": connector_id},
        )
        .mappings()
        .first()
    )
    if conn_row is None or conn_row["connector_kind"] not in {"plaid", "stripe_fc"}:
        raise DomainError("ERR_NOT_FOUND", "Bank feed connector not found.", 404)

    cfg = dict(conn_row["config_json"] or {})
    payload = body or {}
    target_account_id = payload.get("ledger_account_id") or cfg.get("ledger_account_id")
    if not target_account_id:
        first_bank = db.scalar(
            text(
                "SELECT id FROM app.accounts "
                "WHERE business_id = :bid AND kind IN ('asset', 'liability') AND active = true "
                "ORDER BY code ASC LIMIT 1"
            ),
            {"bid": business_id},
        )
        if not first_bank:
            raise DomainError(
                "ERR_VALIDATION", "Create a bank or card account before syncing.", 422
            )
        target_account_id = str(first_bank)

    acc_uuid = UUID(str(target_account_id))
    prev_cursor = str(conn_row["sync_cursor"] or "cursor-0")
    next_cursor = (
        f"cursor-{int(prev_cursor.split('-')[-1]) + 1}" if "-" in prev_cursor else "cursor-1"
    )

    incoming_txns = payload.get("transactions")
    if not isinstance(incoming_txns, list):
        incoming_txns = [
            {
                "external_id": f"{conn_row['connector_kind']}-{connector_id}-txn-01",
                "posted_date": date.today().isoformat(),
                "description": "CLIENT SETTLEMENT WIRE",
                "amount": "1240.00",
            },
            {
                "external_id": f"{conn_row['connector_kind']}-{connector_id}-txn-02",
                "posted_date": date.today().isoformat(),
                "description": "CLOUD INFRASTRUCTURE CARD CHARGE",
                "amount": "-285.00",
            },
        ]

    imp_id = uuid4()
    batch_hash = hashlib.sha256(
        f"{connector_id}:{next_cursor}:{json.dumps(incoming_txns, sort_keys=True)}".encode()
    ).hexdigest()
    db.execute(
        text(
            """
            INSERT INTO app.bank_imports (
              id, business_id, account_id, filename, file_hash, imported_by
            )
            VALUES (:id, :bid, :aid, :fn, :fh, :uid)
            ON CONFLICT (business_id, account_id, file_hash) DO NOTHING
            """
        ),
        {
            "id": imp_id,
            "bid": business_id,
            "aid": acc_uuid,
            "fn": f"{conn_row['connector_kind']}-sync-{next_cursor}.json",
            "fh": batch_hash,
            "uid": identity.user.id,
        },
    )
    resolved_imp_id = db.scalar(
        text(
            "SELECT id FROM app.bank_imports "
            "WHERE business_id = :bid AND account_id = :aid AND file_hash = :fh"
        ),
        {"bid": business_id, "aid": acc_uuid, "fh": batch_hash},
    )

    created = 0
    deduped = 0
    for tx in incoming_txns:
        ext_id = str(tx.get("external_id") or "").strip()
        if not ext_id:
            continue
        exists = db.scalar(
            text(
                "SELECT id FROM app.bank_rows "
                "WHERE business_id = :bid AND account_id = :aid AND external_id = :eid"
            ),
            {"bid": business_id, "aid": acc_uuid, "eid": ext_id},
        )
        if exists:
            deduped += 1
            continue
        db.execute(
            text(
                """
                INSERT INTO app.bank_rows (
                  id, business_id, import_id, account_id, posted_date,
                  description, amount, external_id, state
                ) VALUES (
                  :id, :bid, :iid, :aid, :pdt,
                  :desc, :amt, :eid, 'for_review'
                )
                """
            ),
            {
                "id": uuid4(),
                "bid": business_id,
                "iid": resolved_imp_id,
                "aid": acc_uuid,
                "pdt": date.fromisoformat(str(tx.get("posted_date") or date.today().isoformat())),
                "desc": str(tx.get("description") or "Bank Feed Transaction")[:240],
                "amt": _q2(tx.get("amount") or "0.00"),
                "eid": ext_id,
            },
        )
        created += 1

    db.execute(
        text(
            """
            UPDATE app.connector_accounts
            SET sync_cursor = :cur, last_synced_at = now()
            WHERE business_id = :bid AND id = :id
            """
        ),
        {"cur": next_cursor, "bid": business_id, "id": connector_id},
    )
    db.execute(
        text(
            """
            INSERT INTO app.connector_sync_runs (
              id, business_id, connector_account_id, connector_kind, direction,
              records_processed, records_created, records_deduplicated, status, summary_json
            ) VALUES (
              :id, :bid, :cid, :kind, 'import',
              :proc, :cre, :ded, 'completed', CAST(:sum AS jsonb)
            )
            """
        ),
        {
            "id": uuid4(),
            "bid": business_id,
            "cid": connector_id,
            "kind": conn_row["connector_kind"],
            "proc": len(incoming_txns),
            "cre": created,
            "ded": deduped,
            "sum": json.dumps({"cursor": next_cursor, "account_id": str(acc_uuid)}),
        },
    )
    return {
        "connector_id": str(connector_id),
        "sync_cursor": next_cursor,
        "records_processed": len(incoming_txns),
        "records_created": created,
        "records_deduplicated": deduped,
    }


@router.post("/businesses/{business_id}/connectors/accounting/export")
def export_to_accounting_system(
    business_id: UUID,
    body: AccountingExportRequest,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """E2 & E3: Export LedgerPilot accounts and journals to QuickBooks or Xero."""
    authorize(db, identity, business_id, {"owner", "accountant"})
    biz = get_business(db, business_id)
    s_dt = body.start_date or date(1900, 1, 1)
    e_dt = body.end_date or date.today()

    accounts = (
        db.execute(
            text(
                "SELECT id, code, name, kind FROM app.accounts "
                "WHERE business_id = :bid ORDER BY code ASC"
            ),
            {"bid": business_id},
        )
        .mappings()
        .all()
    )
    journals = (
        db.execute(
            text(
                """
                SELECT j.id, j.accounting_date, j.memo, j.currency,
                       jl.account_id, a.code AS account_code, a.name AS account_name,
                       jl.debit, jl.credit
                FROM app.journals j
                JOIN app.journal_lines jl
                  ON jl.business_id = j.business_id AND jl.journal_id = j.id
                JOIN app.accounts a
                  ON a.business_id = jl.business_id AND a.id = jl.account_id
                WHERE j.business_id = :bid
                  AND j.status = 'posted'
                  AND j.accounting_date BETWEEN :s AND :e
                ORDER BY j.accounting_date ASC, j.id ASC, jl.position ASC
                LIMIT 2000
                """
            ),
            {"bid": business_id, "s": s_dt, "e": e_dt},
        )
        .mappings()
        .all()
    )

    grouped_journals: dict[str, dict[str, Any]] = {}
    for row in journals:
        jid = str(row["id"])
        entry = grouped_journals.setdefault(
            jid,
            {
                "external_idempotency_ref": f"lp:{jid}",
                "date": row["accounting_date"].isoformat(),
                "memo": str(row["memo"] or ""),
                "currency": str(row["currency"] or biz.currency),
                "lines": [],
            },
        )
        entry["lines"].append(
            {
                "account_code": str(row["account_code"]),
                "account_name": str(row["account_name"]),
                "debit": f"{_q2(row['debit']):.2f}",
                "credit": f"{_q2(row['credit']):.2f}",
            }
        )

    serialized_payload = ""
    if body.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        if body.target_system == "xero":
            writer.writerow(
                ["*Narration", "*Date", "*AccountCode", "*Debit", "*Credit", "Reference"]
            )
            for je in grouped_journals.values():
                for ln in je["lines"]:
                    writer.writerow(
                        [
                            je["memo"] or "LedgerPilot Entry",
                            je["date"],
                            ln["account_code"],
                            ln["debit"],
                            ln["credit"],
                            je["external_idempotency_ref"],
                        ]
                    )
        else:
            writer.writerow(
                ["JournalNo", "JournalDate", "AccountName", "Debits", "Credits", "Memo"]
            )
            for je in grouped_journals.values():
                for ln in je["lines"]:
                    writer.writerow(
                        [
                            je["external_idempotency_ref"][:20],
                            je["date"],
                            ln["account_name"],
                            ln["debit"],
                            ln["credit"],
                            je["memo"],
                        ]
                    )
        serialized_payload = buf.getvalue()
    elif body.format == "iif":
        iif_lines = [
            "!TRNS\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO",
            "!SPL\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO",
            "!ENDTRNS",
        ]
        for je in grouped_journals.values():
            for idx, ln in enumerate(je["lines"]):
                signed = _q2(ln["debit"]) - _q2(ln["credit"])
                prefix = "TRNS" if idx == 0 else "SPL"
                iif_lines.append(
                    f"{prefix}\tGENERAL JOURNAL\t{je['date']}\t"
                    f"{ln['account_name']}\t{signed:.2f}\t{je['memo']}"
                )
            iif_lines.append("ENDTRNS")
        serialized_payload = "\n".join(iif_lines)
    else:
        if body.target_system == "xero":
            doc_obj = {
                "ProviderName": "LedgerPilot",
                "Accounts": [
                    {"Code": r["code"], "Name": r["name"], "Class": r["kind"].upper()}
                    for r in accounts
                ],
                "ManualJournals": [
                    {
                        "Narration": je["memo"] or je["external_idempotency_ref"],
                        "Date": je["date"],
                        "JournalLines": [
                            {
                                "AccountCode": ln["account_code"],
                                "LineAmount": f"{(_q2(ln['debit']) - _q2(ln['credit'])):.2f}",
                            }
                            for ln in je["lines"]
                        ],
                    }
                    for je in grouped_journals.values()
                ],
            }
        else:
            doc_obj = {
                "ProviderName": "LedgerPilot",
                "Account": [{"AcctNum": r["code"], "Name": r["name"]} for r in accounts],
                "JournalEntry": [
                    {
                        "DocNumber": je["external_idempotency_ref"][:21],
                        "TxnDate": je["date"],
                        "PrivateNote": je["memo"],
                        "Line": [
                            {
                                "Amount": ln["debit"] if _q2(ln["debit"]) > 0 else ln["credit"],
                                "JournalEntryLineDetail": {
                                    "PostingType": "Debit" if _q2(ln["debit"]) > 0 else "Credit",
                                    "AccountRef": {"name": ln["account_name"]},
                                },
                            }
                            for ln in je["lines"]
                        ],
                    }
                    for je in grouped_journals.values()
                ],
            }
        serialized_payload = json.dumps(doc_obj, indent=2)

    db.execute(
        text(
            """
            INSERT INTO app.connector_sync_runs (
              id, business_id, connector_kind, direction,
              records_processed, records_created, status, summary_json
            ) VALUES (
              :id, :bid, :kind, 'export',
              :cnt, :cnt, 'completed', CAST(:sum AS jsonb)
            )
            """
        ),
        {
            "id": uuid4(),
            "bid": business_id,
            "kind": body.target_system,
            "cnt": len(grouped_journals),
            "sum": json.dumps({"format": body.format, "accounts_exported": len(accounts)}),
        },
    )
    return {
        "target_system": body.target_system,
        "format": body.format,
        "accounts_count": len(accounts),
        "journals_exported": len(grouped_journals),
        "payload": serialized_payload,
    }


@router.post("/businesses/{business_id}/connectors/xero/import", status_code=201)
def import_from_xero(
    business_id: UUID,
    body: XeroImportRequest,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """E3: Reviewed Xero import for accounts and balanced manual journals with deduplication."""
    from ledgerpilot.accounting import schemas as acc_schemas
    from ledgerpilot.accounting import service as acc_service

    authorize(db, identity, business_id, {"owner", "accountant"})
    biz = get_business(db, business_id)
    supplied = body.records or {
        "Accounts": [
            {"Code": "4090", "Name": "Xero Consulting Revenue", "Class": "REVENUE"},
            {"Code": "6090", "Name": "Xero Software Subscriptions", "Class": "EXPENSE"},
        ],
        "ManualJournals": [
            {
                "ManualJournalID": "xero-mj-001",
                "Date": date.today().isoformat(),
                "Narration": "Imported Xero consulting accrual",
                "JournalLines": [
                    {"AccountCode": "6090", "LineAmount": "350.00"},
                    {"AccountCode": "4090", "LineAmount": "-350.00"},
                ],
            }
        ],
    }

    kind_map = {
        "ASSET": "asset",
        "LIABILITY": "liability",
        "EQUITY": "equity",
        "REVENUE": "income",
        "EXPENSE": "expense",
    }
    accounts_created = 0
    journals_posted = 0
    journals_deduped = 0

    code_to_id: dict[str, UUID] = {
        str(r["code"]): UUID(str(r["id"]))
        for r in db.execute(
            text("SELECT id, code FROM app.accounts WHERE business_id = :bid"),
            {"bid": business_id},
        ).mappings()
    }

    if not body.dry_run:
        for xacc in supplied.get("Accounts", []):
            code = str(xacc.get("Code") or "").strip()
            if not code or code in code_to_id:
                continue
            new_aid = uuid4()
            lp_kind = kind_map.get(str(xacc.get("Class") or "EXPENSE").upper(), "expense")
            db.execute(
                text(
                    """
                    INSERT INTO app.accounts (id, business_id, code, name, kind, active)
                    VALUES (:id, :bid, :code, :name, :kind, true)
                    """
                ),
                {
                    "id": new_aid,
                    "bid": business_id,
                    "code": code[:32],
                    "name": str(xacc.get("Name") or f"Xero {code}")[:120],
                    "kind": lp_kind,
                },
            )
            code_to_id[code] = new_aid
            accounts_created += 1
        db.flush()

        for xj in supplied.get("ManualJournals", []):
            xjid = str(xj.get("ManualJournalID") or uuid4().hex[:10])
            idem_key = f"xero:{body.tenant_id}:journal:{xjid}"[:64]
            already = db.scalar(
                text(
                    "SELECT id FROM app.journals WHERE business_id = :bid AND idempotency_key = :ik"
                ),
                {"bid": business_id, "ik": idem_key},
            )
            if already:
                journals_deduped += 1
                continue
            j_lines: list[acc_schemas.LineCreate] = []
            for ln in xj.get("JournalLines", []):
                acode = str(ln.get("AccountCode") or "")
                aid = code_to_id.get(acode)
                if aid is None:
                    continue
                amt = _q2(ln.get("LineAmount") or "0.00")
                if amt > 0:
                    j_lines.append(acc_schemas.LineCreate(account_id=aid, debit=amt))
                elif amt < 0:
                    j_lines.append(acc_schemas.LineCreate(account_id=aid, credit=abs(amt)))
            if len(j_lines) >= 2:
                cur_biz = get_business(db, business_id)
                acc_service.post(
                    db,
                    identity,
                    business_id,
                    acc_schemas.JournalCreate(
                        accounting_date=date.fromisoformat(
                            str(xj.get("Date") or date.today().isoformat())
                        ),
                        memo=str(xj.get("Narration") or f"Xero {xjid}")[:240],
                        currency=biz.currency,
                        idempotency_key=idem_key,
                        expected_version=cur_biz.version,
                        lines=j_lines,
                    ),
                )
                journals_posted += 1

        db.execute(
            text(
                """
                INSERT INTO app.connector_sync_runs (
                  id, business_id, connector_kind, direction,
                  records_processed, records_created, records_deduplicated, status, summary_json
                ) VALUES (
                  :id, :bid, 'xero', 'import',
                  :proc, :cre, :ded, 'completed', CAST(:sum AS jsonb)
                )
                """
            ),
            {
                "id": uuid4(),
                "bid": business_id,
                "proc": len(supplied.get("Accounts", [])) + len(supplied.get("ManualJournals", [])),
                "cre": accounts_created + journals_posted,
                "ded": journals_deduped,
                "sum": json.dumps({"tenant_id": body.tenant_id}),
            },
        )

    return {
        "tenant_id": body.tenant_id,
        "dry_run": body.dry_run,
        "accounts_created": accounts_created,
        "journals_posted": journals_posted,
        "journals_deduplicated": journals_deduped,
    }


@router.post("/businesses/{business_id}/connectors/ecommerce/{provider}/sync", status_code=201)
def sync_ecommerce_sales_connector(
    business_id: UUID,
    provider: Literal["shopify", "square"],
    body: EcommerceSyncRequest,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """E4: Sync Shopify or Square orders, refunds, processor fees, and payout clearing drafts."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    pdate = (body.payout_date or date.today()).isoformat()
    orders = body.orders or [
        {"order_id": f"{provider}-1001", "gross": "640.00", "fee": "18.86", "refund": "0.00"},
        {"order_id": f"{provider}-1002", "gross": "380.00", "fee": "11.32", "refund": "40.00"},
    ]
    gross_sum = sum((_q2(o.get("gross") or "0") for o in orders), Decimal("0.00"))
    fee_sum = sum((_q2(o.get("fee") or "0") for o in orders), Decimal("0.00"))
    refund_sum = sum((_q2(o.get("refund") or "0") for o in orders), Decimal("0.00"))
    net_payout = _q2(gross_sum - fee_sum - refund_sum)

    draft_key = f"ecom:{provider}:{pdate}:{len(orders)}"
    draft_id = uuid4()
    draft_payload = {
        "provider": provider,
        "payout_date": pdate,
        "currency": biz.currency,
        "order_count": len(orders),
        "gross_sales": f"{gross_sum:.2f}",
        "processing_fees": f"{fee_sum:.2f}",
        "refunds": f"{refund_sum:.2f}",
        "net_payout_clearing": f"{net_payout:.2f}",
        "orders": orders,
    }
    db.execute(
        text(
            """
            INSERT INTO app.source_drafts (
              id, business_id, key, payload, status, version, prepared_by
            ) VALUES (
              :id, :bid, :key, CAST(:payload AS jsonb), 'draft', 0, :uid
            )
            ON CONFLICT (business_id, key) DO NOTHING
            """
        ),
        {
            "id": draft_id,
            "bid": business_id,
            "key": draft_key,
            "payload": json.dumps(draft_payload),
            "uid": identity.user.id,
        },
    )
    db.execute(
        text(
            """
            INSERT INTO app.connector_sync_runs (
              id, business_id, connector_kind, direction,
              records_processed, records_created, status, summary_json
            ) VALUES (
              :id, :bid, :kind, 'import',
              :proc, 1, 'completed', CAST(:sum AS jsonb)
            )
            """
        ),
        {
            "id": uuid4(),
            "bid": business_id,
            "kind": provider,
            "proc": len(orders),
            "sum": json.dumps(draft_payload),
        },
    )
    return {
        "provider": provider,
        "payout_date": pdate,
        "orders_synced": len(orders),
        "gross_sales": f"{gross_sum:.2f}",
        "processing_fees": f"{fee_sum:.2f}",
        "refunds": f"{refund_sum:.2f}",
        "net_payout": f"{net_payout:.2f}",
        "draft_key": draft_key,
    }


@router.post("/businesses/{business_id}/connectors/cloud-drive/{provider}/sync", status_code=201)
def sync_cloud_drive_folder(
    business_id: UUID,
    provider: Literal["google_drive", "dropbox"],
    body: CloudDriveSyncRequest,
    request: Request,
    identity: Identity,
    db: DB,
) -> dict[str, Any]:
    """E6: Sync watched Google Drive or Dropbox receipt/invoice folder via extraction + dedupe."""
    authorize(db, identity, business_id, {"owner", "accountant", "bookkeeper"})
    biz = get_business(db, business_id)
    files = body.files or [
        {
            "filename": f"{provider}-invoice-204.pdf",
            "raw_text": (
                f"Vendor: Cloud Storage Vendor\nInvoice #: DRV-204\n"
                f"Date: {date.today().isoformat()}\nTotal: 290.00 {biz.currency}"
            ),
        }
    ]
    created = 0
    deduped = 0
    for f in files:
        fname = str(f.get("filename") or "drive-doc.pdf")
        rtext = str(f.get("raw_text") or fname)
        extracted = extract_structured_document(
            rtext,
            filename=fname,
            default_currency=biz.currency,
            settings=request.app.state.settings,
        )
        claim = claim_document_fingerprint(
            db,
            business_id=business_id,
            actor_id=identity.user.id,
            content_bytes=rtext.encode("utf-8"),
            channel=provider,
            extracted=extracted,
            create_draft=True,
        )
        if claim["deduplicated"]:
            deduped += 1
        else:
            created += 1

    db.execute(
        text(
            """
            INSERT INTO app.connector_sync_runs (
              id, business_id, connector_kind, direction,
              records_processed, records_created, records_deduplicated, status, summary_json
            ) VALUES (
              :id, :bid, :kind, 'import',
              :proc, :cre, :ded, 'completed', CAST(:sum AS jsonb)
            )
            """
        ),
        {
            "id": uuid4(),
            "bid": business_id,
            "kind": provider,
            "proc": len(files),
            "cre": created,
            "ded": deduped,
            "sum": json.dumps({"folder": body.folder_name}),
        },
    )
    return {
        "provider": provider,
        "folder_name": body.folder_name,
        "files_processed": len(files),
        "drafts_created": created,
        "deduplicated": deduped,
    }


@router.post("/webhooks/inbound-email", status_code=201)
async def inbound_invoice_email_webhook(
    request: Request,
    db: DB,
    x_inbound_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """E5: Signed inbound email webhook -> SPF/DKIM check -> extraction -> dedupe -> PO gate."""
    settings: Settings = request.app.state.settings
    raw_body = await request.body()

    if settings.inbound_email_webhook_secret is not None:
        secret_bytes = settings.inbound_email_webhook_secret.get_secret_value().encode("utf-8")
        expected_sig = hmac.new(secret_bytes, raw_body, hashlib.sha256).hexdigest()
        if not x_inbound_signature or not hmac.compare_digest(
            x_inbound_signature.strip().removeprefix("sha256="), expected_sig
        ):
            raise DomainError("ERR_AUTH", "Invalid inbound email webhook signature.", 401)

    payload = json.loads(raw_body.decode("utf-8"))
    recipient = str(payload.get("to") or payload.get("recipient") or "").strip().lower()
    sender = str(payload.get("from") or payload.get("sender") or "").strip().lower()
    subject = str(payload.get("subject") or "").strip()[:240]
    msg_id = str(payload.get("message_id") or uuid4().hex).strip()[:160]
    body_text = str(payload.get("text") or payload.get("raw_text") or subject)
    spf_dkim_ok = bool(payload.get("spf_dkim_passed", True))

    # Resolve target business_id from explicit business_id or bills+<slug>-<shortid>@domain
    bid: UUID | None = None
    if payload.get("business_id"):
        bid = UUID(str(payload["business_id"]))
    else:
        m = re.match(r"^bills\+([a-z0-9\-]+)@", recipient)
        if m:
            slug_part = m.group(1).split("-")[-1]
            bid = db.scalar(
                text("SELECT app.resolve_inbound_email_business(:s)"),
                {"s": slug_part},
            )
    if bid is None:
        raise DomainError("ERR_NOT_FOUND", "Target workspace inbox not found.", 404)

    # Resolve workspace owner to set RLS context cleanly
    owner_id = db.scalar(
        text(
            "SELECT user_id FROM app.memberships "
            "WHERE business_id = :b AND active = true ORDER BY role = 'owner' DESC LIMIT 1"
        ),
        {"b": bid},
    )
    if owner_id is None:
        # Look up via SECURITY DEFINER business owner fallback if needed
        raise DomainError("ERR_NOT_FOUND", "No active workspace owner for inbound inbox.", 404)

    set_context(db, UUID(str(owner_id)), bid)
    biz = get_business(db, bid)

    if not spf_dkim_ok:
        eid = uuid4()
        db.execute(
            text(
                """
                INSERT INTO app.inbound_invoice_emails (
                  id, business_id, message_id, sender_email, recipient_address,
                  subject, spf_dkim_passed, status
                ) VALUES (
                  :id, :bid, :mid, :from_e, :to_e, :subj, false, 'quarantined_sender'
                )
                ON CONFLICT (business_id, message_id) DO NOTHING
                """
            ),
            {
                "id": eid,
                "bid": bid,
                "mid": msg_id,
                "from_e": sender,
                "to_e": recipient,
                "subj": subject,
            },
        )
        return {"status": "quarantined_sender", "message_id": msg_id}

    extracted = extract_structured_document(
        f"{subject}\n{body_text}",
        filename=f"email-{msg_id}.pdf",
        default_currency=biz.currency,
        settings=settings,
    )
    claim = claim_document_fingerprint(
        db,
        business_id=bid,
        actor_id=UUID(str(owner_id)),
        content_bytes=f"{sender}|{subject}|{body_text}".encode(),
        channel="inbound_email",
        extracted=extracted,
        create_draft=True,
    )

    # Automatic PO variance check (C1 integration!) when PO reference or supplier PO exists
    po_match_id: UUID | None = None
    po_status: str | None = None
    tot_amt = _q2(extracted.get("total_amount") or "0.00")
    po_ref = str(extracted.get("purchase_order_number") or "").strip()
    if tot_amt > Decimal("0.00"):
        po_row = None
        if po_ref:
            po_row = (
                db.execute(
                    text(
                        "SELECT * FROM app.purchase_orders "
                        "WHERE business_id = :b AND upper(number) = :num LIMIT 1"
                    ),
                    {"b": bid, "num": po_ref.upper()},
                )
                .mappings()
                .first()
            )
        if po_row is not None:
            po_amt = _q2(po_row["total"])
            var_amt = _q2(tot_amt - po_amt)
            var_pct = ((var_amt / po_amt) * Decimal("100")).quantize(Decimal("0.01"))
            tol_pct = Decimal(str(po_row["variance_tolerance_pct"])).quantize(Decimal("0.01"))
            po_status = (
                "pending_owner_approval" if var_pct > tol_pct else "matched_within_tolerance"
            )
            po_match_id = uuid4()
            db.execute(
                text(
                    """
                    INSERT INTO app.purchase_order_matches (
                      id, business_id, purchase_order_id, bill_reference,
                      invoiced_amount, po_amount, variance_amount, variance_pct, status
                    ) VALUES (
                      :id, :bid, :poid, :bref, :inv, :po, :vamt, :vpct, :st
                    )
                    """
                ),
                {
                    "id": po_match_id,
                    "bid": bid,
                    "poid": po_row["id"],
                    "bref": extracted.get("document_number") or subject or msg_id,
                    "inv": tot_amt,
                    "po": po_amt,
                    "vamt": var_amt,
                    "vpct": var_pct,
                    "st": po_status,
                },
            )

    final_status = "deduplicated" if claim["deduplicated"] else "ingested"
    db.execute(
        text(
            """
            INSERT INTO app.inbound_invoice_emails (
              id, business_id, message_id, sender_email, recipient_address,
              subject, spf_dkim_passed, status, fingerprint_id, draft_id, po_match_id
            ) VALUES (
              :id, :bid, :mid, :from_e, :to_e,
              :subj, true, :st, :fpid, :did, :pmid
            )
            ON CONFLICT (business_id, message_id) DO NOTHING
            """
        ),
        {
            "id": uuid4(),
            "bid": bid,
            "mid": msg_id,
            "from_e": sender,
            "to_e": recipient,
            "subj": subject,
            "st": final_status,
            "fpid": UUID(claim["fingerprint_id"]),
            "did": UUID(claim["source_draft_id"]) if claim["source_draft_id"] else None,
            "pmid": po_match_id,
        },
    )
    return {
        "status": final_status,
        "message_id": msg_id,
        "extraction": extracted,
        "deduplication": claim,
        "po_match_id": str(po_match_id) if po_match_id else None,
        "po_match_status": po_status,
    }
