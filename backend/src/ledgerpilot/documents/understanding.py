"""Structured document understanding (invoices, receipts, statements) and deduplication."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.config import Settings

ALLOWED_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD", "PKR", "AED", "SAR", "INR", "SGD"}


def _q2(val: Decimal | str | int | float) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"))


def _norm_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def classify_document_type(raw_text: str, filename: str = "") -> str:
    hay = f"{filename}\n{raw_text}".lower()
    if any(k in hay for k in ("credit note", "credit memo", "avoir", "gutschrift")):
        return "credit_note"
    if any(
        k in hay
        for k in ("bank statement", "opening balance", "closing balance", "statement period")
    ):
        return "bank_statement"
    if any(
        k in hay
        for k in ("agreement", "master service", "contract", "scope of work", "retainer rate")
    ):
        return "contract"
    if any(
        k in hay
        for k in ("receipt", "thank you for your purchase", "card ending", "pos ", "gratuity")
    ):
        return "receipt"
    if any(k in hay for k in ("bill no", "supplier:")):
        return "bill"
    if any(
        k in hay
        for k in (
            "invoice",
            "facture",
            "bill to",
            "amount due",
            "subtotal",
            "purchase order",
            "inv-",
        )
    ):
        return "invoice"
    return (
        "receipt"
        if any(ext in filename.lower() for ext in (".jpg", ".jpeg", ".png"))
        else "invoice"
    )


_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_flexible_date(token: str) -> date | None:
    s = token.strip().replace(",", "")
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            return None
    m_slash = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m_slash:
        a, b, y = int(m_slash.group(1)), int(m_slash.group(2)), int(m_slash.group(3))
        try:
            # Prefer MM/DD/YYYY unless first component > 12 (DD/MM/YYYY)
            return date(y, b, a) if a > 12 else date(y, a, b)
        except ValueError:
            return None
    m_named = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})\s+(\d{4})$", s)
    if m_named:
        month = _MONTH_NAMES.get(m_named.group(1).lower())
        if month:
            try:
                return date(int(m_named.group(3)), month, int(m_named.group(2)))
            except ValueError:
                return None
    m_dmy = re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})$", s)
    if m_dmy:
        month = _MONTH_NAMES.get(m_dmy.group(2).lower())
        if month:
            try:
                return date(int(m_dmy.group(3)), month, int(m_dmy.group(1)))
            except ValueError:
                return None
    return None


def _add_months_inclusive_end(start: date, months: int) -> date:
    from datetime import timedelta

    month_idx = (start.month - 1) + months
    year = start.year + month_idx // 12
    month = (month_idx % 12) + 1
    day = min(
        start.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return date(year, month, day) - timedelta(days=1)


def extract_service_period(raw_text: str) -> dict[str, Any] | None:
    """Detect multi-month service or subscription periods for amortization schedules."""
    date_atom = (
        r"(?:\d{4}-\d{2}-\d{2}"
        r"|\d{1,2}/\d{1,2}/\d{4}"
        r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4})"
    )
    range_pat = re.compile(
        rf"({date_atom})\s*(?:to|through|thru|\-|–|—)\s*({date_atom})",
        re.IGNORECASE,
    )
    for m in range_pat.finditer(raw_text):
        d1 = _parse_flexible_date(m.group(1))
        d2 = _parse_flexible_date(m.group(2))
        if d1 and d2 and d2 > d1:
            months = (
                (d2.year - d1.year) * 12
                + (d2.month - d1.month)
                + (1 if d2.day >= d1.day - 1 else 0)
            )
            if months >= 2:
                return {
                    "start_date": d1.isoformat(),
                    "end_date": d2.isoformat(),
                    "months": months,
                    "raw_match": m.group(0).strip(),
                    "suggested_schedule_type": "prepaid_expense",
                }

    dur_pat = re.compile(
        rf"(?:(\d{{1,2}})[\-\s]*month|annual|1[\-\s]*year)\s+"
        rf"(?:subscription|plan|license|coverage|policy|retainer|term)"
        rf"(?:[^\n]{{0,40}}?(?:starting|effective|from|beginning)\s*[:\-]?\s*({date_atom}))?",
        re.IGNORECASE,
    )
    m_dur = dur_pat.search(raw_text)
    if m_dur:
        months = int(m_dur.group(1)) if m_dur.group(1) else 12
        start = _parse_flexible_date(m_dur.group(2)) if m_dur.group(2) else None
        if start is None:
            iso_any = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", raw_text)
            start = _parse_flexible_date(iso_any.group(1)) if iso_any else date.today()
        if start and months >= 2:
            end = _add_months_inclusive_end(start, months)
            return {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "months": months,
                "raw_match": m_dur.group(0).strip(),
                "suggested_schedule_type": "prepaid_expense",
            }
    return None


def _find_word_box(
    ocr_words: list[dict[str, Any]],
    needle: str,
    default_box: tuple[float, float, float, float],
) -> dict[str, Any]:
    clean_needle = needle.strip().lower().replace(",", "")
    if clean_needle:
        for w in ocr_words:
            w_val = str(w.get("value") or w.get("text") or "").strip().lower().replace(",", "")
            if w_val and (w_val in clean_needle or clean_needle in w_val):
                return {
                    "page": int(w.get("page") or 1),
                    "left": round(float(w.get("left") or default_box[0]), 4),
                    "top": round(float(w.get("top") or default_box[1]), 4),
                    "right": round(float(w.get("right") or default_box[2]), 4),
                    "bottom": round(float(w.get("bottom") or default_box[3]), 4),
                }
    return {
        "page": 1,
        "left": default_box[0],
        "top": default_box[1],
        "right": default_box[2],
        "bottom": default_box[3],
    }


def extract_structured_document(
    raw_text: str,
    *,
    filename: str = "",
    ocr_words: list[dict[str, Any]] | None = None,
    default_currency: str = "USD",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Turn OCR text + word bounding boxes into validated structured fields with pixel regions."""
    words = ocr_words or []
    doc_type = classify_document_type(raw_text, filename)
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    # Vendor extraction: explicit "Vendor:" / "From:" label or first non-generic header line
    vendor = ""
    vendor_m = re.search(
        r"(?:vendor|supplier|fournisseur|lieferant|merchant|from)\s*[:\-]\s*([^\n]{2,80})",
        raw_text,
        re.I,
    )
    if vendor_m:
        vendor = vendor_m.group(1).strip()
    elif lines:
        for ln in lines[:4]:
            if not re.search(r"\b(invoice|receipt|statement|tax|date|page)\b", ln, re.I):
                vendor = ln[:80]
                break
        if not vendor:
            vendor = lines[0][:80]

    # Document / Invoice number
    doc_num = ""
    num_m = re.search(
        r"(?:credit\s+note\s*(?:no|#)?|facture\s*(?:no|#)?|invoice\s*(?:no|#|number)?|bill\s*(?:no|#)?|receipt\s*#|ref)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,30})",
        raw_text,
        re.I,
    )
    if num_m:
        doc_num = num_m.group(1).strip()

    # Purchase order reference (links into Phase C PO variance check!)
    po_number = ""
    po_m = re.search(
        r"(?:p\.?o\.?\s*(?:no|#|number)?|purchase\s+order)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{1,30})",
        raw_text,
        re.I,
    )
    if po_m:
        po_number = po_m.group(1).strip()

    # Dates (YYYY-MM-DD or MM/DD/YYYY)
    iso_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", raw_text)
    issue_date_str = iso_dates[0] if iso_dates else date.today().isoformat()
    due_date_str = iso_dates[1] if len(iso_dates) > 1 else issue_date_str
    try:
        date.fromisoformat(issue_date_str)
    except ValueError:
        issue_date_str = date.today().isoformat()

    # Currency
    ccy = default_currency.upper()
    ccy_m = re.search(r"\b(USD|EUR|GBP|CAD|AUD|PKR|AED|SAR|INR|SGD)\b", raw_text.upper())
    if ccy_m and ccy_m.group(1) in ALLOWED_CURRENCIES:
        ccy = ccy_m.group(1)

    # Monetary amounts (Total, Subtotal, Tax)
    def _extract_labeled_amount(pattern: str) -> Decimal | None:
        m = re.search(pattern, raw_text, re.I)
        if not m:
            return None
        cleaned = m.group(1).replace(",", "").strip()
        try:
            return _q2(cleaned)
        except (InvalidOperation, ValueError):
            return None

    subtotal = _extract_labeled_amount(
        r"\b(?:subtotal|sous-total)\s*[:\-]?\s*[$€£]?\s*([0-9][0-9,]*\.\d{2})"
    )
    tax_amt = _extract_labeled_amount(
        r"\b(?:tax|vat|gst|tva|mwst)\s*[:\-]?\s*[$€£]?\s*([0-9][0-9,]*\.\d{2})"
    )
    total_amt = _extract_labeled_amount(
        r"\b(?:total(?:\s+due|\s+amount)?|amount\s+due|grand\s+total)\s*[:\-]?\s*[$€£]?\s*([0-9][0-9,]*\.\d{2})"
    )

    if total_amt is None:
        all_amounts: list[Decimal] = []
        for raw_num in re.findall(r"\b([0-9]{1,9}(?:,[0-9]{3})*\.\d{2})\b", raw_text):
            try:
                all_amounts.append(_q2(raw_num.replace(",", "")))
            except (InvalidOperation, ValueError):
                continue
        total_amt = max(all_amounts) if all_amounts else Decimal("0.00")

    if subtotal is None:
        subtotal = _q2(total_amt - (tax_amt or Decimal("0.00")))
    if tax_amt is None:
        tax_amt = _q2(max(Decimal("0.00"), total_amt - subtotal))

    # Arithmetic validation check
    validation_issues: list[str] = []
    if _q2(subtotal + tax_amt) != _q2(total_amt):
        validation_issues.append(
            f"Subtotal ({subtotal:.2f}) + Tax ({tax_amt:.2f}) != Total ({total_amt:.2f})"
        )
    if total_amt <= Decimal("0.00"):
        validation_issues.append("No positive monetary total detected on document")

    base_conf = Decimal("0.9300") if (vendor and total_amt > 0) else Decimal("0.6800")
    if doc_num:
        base_conf += Decimal("0.0300")
    if validation_issues:
        base_conf -= Decimal("0.2200")
    overall_conf = max(Decimal("0.3500"), min(Decimal("0.9900"), base_conf))

    structured_fields = [
        {
            "name": "vendor_name",
            "value": vendor or "Unknown Vendor",
            "value_type": "text",
            "confidence": float(overall_conf),
            "semantic_role": "vendor",
            **_find_word_box(words, vendor, (0.06, 0.06, 0.52, 0.12)),
        },
        {
            "name": "document_number",
            "value": doc_num or "UNNUMBERED",
            "value_type": "text",
            "confidence": float(overall_conf if doc_num else Decimal("0.55")),
            "semantic_role": "document_number",
            **_find_word_box(words, doc_num, (0.62, 0.08, 0.92, 0.13)),
        },
        {
            "name": "issue_date",
            "value": issue_date_str,
            "value_type": "date",
            "confidence": float(overall_conf),
            "semantic_role": "issue_date",
            **_find_word_box(words, issue_date_str, (0.62, 0.14, 0.90, 0.19)),
        },
        {
            "name": "subtotal",
            "value": f"{subtotal:.2f}",
            "value_type": "amount",
            "confidence": float(overall_conf),
            "semantic_role": "subtotal",
            **_find_word_box(words, f"{subtotal:.2f}", (0.65, 0.72, 0.92, 0.77)),
        },
        {
            "name": "tax_amount",
            "value": f"{tax_amt:.2f}",
            "value_type": "amount",
            "confidence": float(overall_conf),
            "semantic_role": "tax",
            **_find_word_box(words, f"{tax_amt:.2f}", (0.65, 0.78, 0.92, 0.83)),
        },
        {
            "name": "total_amount",
            "value": f"{total_amt:.2f}",
            "value_type": "amount",
            "confidence": float(overall_conf),
            "semantic_role": "total",
            **_find_word_box(words, f"{total_amt:.2f}", (0.62, 0.84, 0.94, 0.90)),
        },
    ]
    if po_number:
        structured_fields.append(
            {
                "name": "purchase_order_number",
                "value": po_number,
                "value_type": "text",
                "confidence": 0.94,
                "semantic_role": "po_reference",
                **_find_word_box(words, po_number, (0.06, 0.18, 0.44, 0.23)),
            }
        )

    service_period = extract_service_period(raw_text)
    return {
        "document_type": doc_type,
        "vendor_name": vendor or "Unknown Vendor",
        "document_number": doc_num,
        "purchase_order_number": po_number,
        "issue_date": issue_date_str,
        "document_date": issue_date_str,
        "due_date": due_date_str,
        "currency": ccy,
        "subtotal": f"{subtotal:.2f}",
        "subtotal_amount": f"{subtotal:.2f}",
        "tax_amount": f"{tax_amt:.2f}",
        "total_amount": f"{total_amt:.2f}",
        "overall_confidence": str(overall_conf),
        "validation_passed": len(validation_issues) == 0,
        "arithmetic_consistent": len(validation_issues) == 0,
        "validation_issues": validation_issues,
        "uncertainty_flags": ["ARITHMETIC_MISMATCH"] if validation_issues else [],
        "requires_human_review": True,
        "model_version": "deterministic-grounded-extractor-v1",
        "service_period": service_period,
        "fields": structured_fields,
    }


def claim_document_fingerprint(
    db: Session,
    *,
    business_id: UUID,
    actor_id: UUID,
    content_bytes: bytes,
    channel: str,
    extracted: dict[str, Any],
    evidence_id: UUID | None = None,
    create_draft: bool = True,
) -> dict[str, Any]:
    """Cross-channel deduplication: an identical document across channels creates 1 draft."""
    content_sha = hashlib.sha256(content_bytes).hexdigest()
    vendor_norm = _norm_token(str(extracted.get("vendor_name") or ""))
    doc_num_norm = _norm_token(str(extracted.get("document_number") or ""))
    tot_dec = _q2(extracted.get("total_amount") or "0.00")
    ccy = str(extracted.get("currency") or "USD").upper()

    semantic_key: str | None = None
    if vendor_norm and doc_num_norm and doc_num_norm != "unnumbered" and tot_dec > Decimal("0.00"):
        semantic_key = f"{vendor_norm}|{doc_num_norm}|{tot_dec:.2f}|{ccy}"

    existing = (
        db.execute(
            text(
                """
                SELECT id, content_sha256, semantic_key, first_evidence_id,
                       source_draft_id, trade_document_id, channels
                FROM app.document_fingerprints
                WHERE business_id = :bid
                  AND (
                    content_sha256 = :sha
                    OR (:sem IS NOT NULL AND semantic_key = :sem)
                  )
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"bid": business_id, "sha": content_sha, "sem": semantic_key},
        )
        .mappings()
        .first()
    )

    if existing is not None:
        cur_channels = list(existing["channels"] or [])
        if channel not in cur_channels:
            cur_channels.append(channel)
            db.execute(
                text(
                    """
                    UPDATE app.document_fingerprints
                    SET channels = CAST(:ch AS jsonb),
                        updated_at = now()
                    WHERE business_id = :bid AND id = :id
                    """
                ),
                {"ch": json.dumps(cur_channels), "bid": business_id, "id": existing["id"]},
            )
        return {
            "deduplicated": True,
            "fingerprint_id": str(existing["id"]),
            "first_evidence_id": (
                str(existing["first_evidence_id"]) if existing["first_evidence_id"] else None
            ),
            "source_draft_id": (
                str(existing["source_draft_id"]) if existing["source_draft_id"] else None
            ),
            "channels": cur_channels,
        }

    draft_id: UUID | None = None
    if create_draft and tot_dec > Decimal("0.00"):
        draft_id = uuid4()
        draft_key = f"doc:{content_sha[:24]}"
        draft_payload = {
            "kind": "bill" if extracted.get("document_type") != "contract" else "memo",
            "vendor_name": extracted.get("vendor_name"),
            "number": extracted.get("document_number") or f"AUTO-{content_sha[:6].upper()}",
            "purchase_order_number": extracted.get("purchase_order_number") or "",
            "issue_date": extracted.get("issue_date"),
            "due_date": extracted.get("due_date"),
            "currency": ccy,
            "subtotal": extracted.get("subtotal"),
            "tax_amount": extracted.get("tax_amount"),
            "total": f"{tot_dec:.2f}",
            "evidence_id": str(evidence_id) if evidence_id else None,
            "channel": channel,
            "fields": extracted.get("fields") or [],
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
                "uid": actor_id,
            },
        )
        # Resolve actual draft_id if ON CONFLICT hit
        actual_draft = db.scalar(
            text("SELECT id FROM app.source_drafts WHERE business_id = :bid AND key = :k"),
            {"bid": business_id, "k": draft_key},
        )
        draft_id = actual_draft if actual_draft else None

    fp_id = uuid4()
    doc_dt: date | None = None
    try:
        if extracted.get("issue_date"):
            doc_dt = date.fromisoformat(str(extracted["issue_date"]))
    except ValueError:
        doc_dt = None

    db.execute(
        text(
            """
            INSERT INTO app.document_fingerprints (
              id, business_id, content_sha256, semantic_key, doc_type,
              vendor_name, doc_number, doc_date, currency, total_amount,
              first_evidence_id, source_draft_id, channels, extracted_fields,
              overall_confidence
            ) VALUES (
              :id, :bid, :sha, :sem, :dtype,
              :vname, :dnum, :ddt, :ccy, :tot,
              :evid, :did, CAST(:ch AS jsonb), CAST(:ef AS jsonb),
              :conf
            )
            """
        ),
        {
            "id": fp_id,
            "bid": business_id,
            "sha": content_sha,
            "sem": semantic_key,
            "dtype": str(extracted.get("document_type") or "invoice"),
            "vname": str(extracted.get("vendor_name") or "")[:160],
            "dnum": str(extracted.get("document_number") or "")[:80],
            "ddt": doc_dt,
            "ccy": ccy,
            "tot": tot_dec,
            "evid": evidence_id,
            "did": draft_id,
            "ch": json.dumps([channel]),
            "ef": json.dumps(extracted),
            "conf": Decimal(str(extracted.get("overall_confidence") or "0.8000")),
        },
    )

    return {
        "deduplicated": False,
        "fingerprint_id": str(fp_id),
        "first_evidence_id": str(evidence_id) if evidence_id else None,
        "source_draft_id": str(draft_id) if draft_id else None,
        "channels": [channel],
    }
