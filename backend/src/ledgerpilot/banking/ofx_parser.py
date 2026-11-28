"""Pure-Python OFX 1.x (SGML) and OFX 2.x (XML) bank/card statement parser (Phase J0)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from ledgerpilot.shared.errors import DomainError


@dataclass(frozen=True)
class OfxTransaction:
    external_id: str
    posted_date: date
    amount: Decimal
    name: str
    memo: str
    description: str
    trn_type: str


@dataclass(frozen=True)
class OfxStatement:
    currency: str
    account_id: str
    transactions: list[OfxTransaction]


def _parse_ofx_date(raw: str) -> date:
    """Parse OFX date strings like 20261115, 20261115120000, or 20261115120000[-5:EST]."""
    cleaned = re.sub(r"\[.*\]$", "", raw.strip())
    digits = re.match(r"^(\d{8})", cleaned)
    if not digits:
        raise DomainError("ERR_VALIDATION", f"Invalid OFX date value: {raw!r}.", 422)
    ymd = digits.group(1)
    try:
        return date(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]))
    except ValueError as exc:
        raise DomainError("ERR_VALIDATION", f"Invalid OFX calendar date: {raw!r}.", 422) from exc


def _parse_ofx_amount(raw: str) -> Decimal:
    cleaned = raw.strip().replace(",", "")
    if not cleaned:
        raise DomainError("ERR_VALIDATION", "OFX transaction is missing TRNAMT.", 422)
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise DomainError("ERR_VALIDATION", f"Invalid OFX amount: {raw!r}.", 422) from exc


def _build_description(name: str, memo: str, trn_type: str) -> str:
    n = " ".join(name.split())
    m = " ".join(memo.split())
    if n and m and m.lower() not in n.lower():
        return f"{n} - {m}"[:250]
    if n:
        return n[:250]
    if m:
        return m[:250]
    return (trn_type or "Bank transaction")[:250]


def _parse_xml_ofx(text: str) -> OfxStatement:
    start = text.find("<OFX")
    if start == -1:
        start = text.lower().find("<ofx")
    if start == -1:
        raise DomainError("ERR_VALIDATION", "File is not a valid OFX document.", 422)
    xml_body = text[start:].strip()
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError as exc:
        raise DomainError("ERR_VALIDATION", f"Malformed OFX XML: {exc}.", 422) from exc

    def _find_text(node: ET.Element, tag: str) -> str:
        for child in node.iter():
            if child.tag.upper().endswith(tag.upper()) and child.text:
                return child.text.strip()
        return ""

    currency = (_find_text(root, "CURDEF") or "USD").upper()[:3]
    account_id = _find_text(root, "ACCTID")
    txns: list[OfxTransaction] = []
    for elem in root.iter():
        if not elem.tag.upper().endswith("STMTTRN"):
            continue
        fields: dict[str, str] = {}
        for child in elem:
            key = child.tag.split("}")[-1].upper()
            fields[key] = (child.text or "").strip()
        fitid = fields.get("FITID", "")
        dtposted = fields.get("DTPOSTED", "")
        trnamt = fields.get("TRNAMT", "")
        if not dtposted or not trnamt:
            raise DomainError(
                "ERR_VALIDATION",
                "OFX STMTTRN entry must contain DTPOSTED and TRNAMT.",
                422,
            )
        posted = _parse_ofx_date(dtposted)
        amount = _parse_ofx_amount(trnamt)
        name = fields.get("NAME", "")
        memo = fields.get("MEMO", "")
        trn_type = fields.get("TRNTYPE", "OTHER").upper()
        external_id = fitid or f"ofx-{posted.isoformat()}-{amount}-{len(txns) + 1}"
        txns.append(
            OfxTransaction(
                external_id=external_id[:120],
                posted_date=posted,
                amount=amount,
                name=name[:120],
                memo=memo[:160],
                description=_build_description(name, memo, trn_type),
                trn_type=trn_type[:24],
            )
        )
    if not txns:
        raise DomainError("ERR_VALIDATION", "No STMTTRN transactions found in OFX file.", 422)
    return OfxStatement(currency=currency, account_id=account_id, transactions=txns)


def _extract_sgml_tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*([^<\r\n]+)", block, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_sgml_ofx(text: str) -> OfxStatement:
    if "<OFX>" not in text.upper():
        raise DomainError("ERR_VALIDATION", "File is not a valid OFX/QFX document.", 422)
    currency = (_extract_sgml_tag(text, "CURDEF") or "USD").upper()[:3]
    account_id = _extract_sgml_tag(text, "ACCTID")
    blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.IGNORECASE | re.DOTALL)
    if not blocks:
        # Handle unclosed SGML <STMTTRN> blocks (some QFX banks omit </STMTTRN>).
        parts = re.split(r"<STMTTRN>", text, flags=re.IGNORECASE)
        blocks = [p.split("</BANKTRANLIST>")[0] for p in parts[1:] if p.strip()]
    txns: list[OfxTransaction] = []
    for idx, block in enumerate(blocks, start=1):
        dtposted = _extract_sgml_tag(block, "DTPOSTED")
        trnamt = _extract_sgml_tag(block, "TRNAMT")
        if not dtposted or not trnamt:
            raise DomainError(
                "ERR_VALIDATION",
                f"OFX transaction #{idx} is missing DTPOSTED or TRNAMT.",
                422,
            )
        posted = _parse_ofx_date(dtposted)
        amount = _parse_ofx_amount(trnamt)
        name = _extract_sgml_tag(block, "NAME")
        memo = _extract_sgml_tag(block, "MEMO")
        trn_type = (_extract_sgml_tag(block, "TRNTYPE") or "OTHER").upper()
        fitid = _extract_sgml_tag(block, "FITID")
        external_id = fitid or f"ofx-{posted.isoformat()}-{amount}-{idx}"
        txns.append(
            OfxTransaction(
                external_id=external_id[:120],
                posted_date=posted,
                amount=amount,
                name=name[:120],
                memo=memo[:160],
                description=_build_description(name, memo, trn_type),
                trn_type=trn_type[:24],
            )
        )
    if not txns:
        raise DomainError("ERR_VALIDATION", "No STMTTRN transactions found in OFX/QFX file.", 422)
    return OfxStatement(currency=currency, account_id=account_id, transactions=txns)


def parse_ofx(raw_bytes: bytes | str) -> OfxStatement:
    """Parse OFX 1.x (SGML/QFX) or OFX 2.x (XML) statement content."""
    if isinstance(raw_bytes, bytes):
        if len(raw_bytes) > 5 * 1024 * 1024:
            raise DomainError("ERR_VALIDATION", "OFX file exceeds 5 MiB limit.", 422)
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin-1", errors="replace")
    else:
        text = raw_bytes
    stripped = text.strip()
    if not stripped:
        raise DomainError("ERR_VALIDATION", "OFX file is empty.", 422)
    if "<?xml" in stripped[:200].lower() or "</TRNAMT>" in stripped.upper():
        try:
            return _parse_xml_ofx(stripped)
        except DomainError:
            return _parse_sgml_ofx(stripped)
    return _parse_sgml_ofx(stripped)


def ofx_to_rows(raw_bytes: bytes | str) -> list[dict[str, Any]]:
    """Return normalized transaction dicts ready for bank import."""
    stmt = parse_ofx(raw_bytes)
    return [
        {
            "external_id": t.external_id,
            "posted_date": t.posted_date.isoformat(),
            "amount": str(t.amount),
            "name": t.name,
            "memo": t.memo,
            "description": t.description,
            "trn_type": t.trn_type,
            "currency": stmt.currency,
        }
        for t in stmt.transactions
    ]
