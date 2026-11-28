"""AI-first merchant normalization, alias cache, and double-entry preview (Phase J1 / Module 8).

Replaces paid Clearbit & Google Places APIs with deterministic normalization +
workspace alias caching + GroqModelAdapter LLM enrichment, with optional
cosmetic Brandfetch logos when BRANDFETCH_CLIENT_ID is configured.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from ledgerpilot.config import Settings

_KNOWN_MERCHANTS: list[tuple[re.Pattern[str], str, str, str]] = [
    (
        re.compile(r"\b(amzn\s*mktp|amazon\s*mktp|amazon\.com|amzn)\b", re.I),
        "Amazon Marketplace",
        "Office Supplies",
        "amazon.com",
    ),
    (
        re.compile(r"\b(aws|amazon\s+web\s+services)\b", re.I),
        "Amazon Web Services",
        "Cloud & Hosting",
        "aws.amazon.com",
    ),
    (
        re.compile(r"\b(google\s*\*?\s*cloud|gcp|google\s*\*?\s*workspace|gsuite)\b", re.I),
        "Google Cloud",
        "Cloud & Hosting",
        "cloud.google.com",
    ),
    (
        re.compile(r"\b(msft|microsoft|azure|office\s*365)\b", re.I),
        "Microsoft",
        "Software & Subscriptions",
        "microsoft.com",
    ),
    (
        re.compile(r"\b(slack|atlassian|jira|figma|notion|linear|github|vercel|heroku)\b", re.I),
        "",
        "Software & Subscriptions",
        "",
    ),
    (
        re.compile(r"\b(stripe|paypal|square\s+inc|adyen)\b", re.I),
        "",
        "Merchant Processing Fees",
        "",
    ),
    (
        re.compile(r"\b(uber\s*\*?\s*eats|doordash|grubhub|deliveroo)\b", re.I),
        "",
        "Meals & Entertainment",
        "",
    ),
    (
        re.compile(
            r"\b(uber|lyft|delta\s*air|united\s*air|american\s*air|marriott|hilton|airbnb)\b", re.I
        ),
        "",
        "Travel & Lodging",
        "",
    ),
    (
        re.compile(r"\b(fedex|ups|usps|dhl)\b", re.I),
        "",
        "Shipping & Freight",
        "",
    ),
    (
        re.compile(r"\b(gusto|adp|paychex|rippling|deel)\b", re.I),
        "",
        "Payroll & Benefits",
        "",
    ),
    (
        re.compile(r"\b(comcast|verizon|at&t|t-mobile|pg&e|con\s*edison)\b", re.I),
        "",
        "Utilities & Telecom",
        "",
    ),
    (
        re.compile(r"\b(staples|office\s*depot|home\s*depot|costco|walmart|target)\b", re.I),
        "",
        "Office Supplies",
        "",
    ),
    (
        re.compile(r"\b(starbucks|dunkin|blue\s*bottle|peet'?s|panera|chipotle)\b", re.I),
        "",
        "Meals & Entertainment",
        "",
    ),
    (
        re.compile(r"\b(meta\s*ads|facebook\s*ads|google\s*ads|linkedin\s*ads)\b", re.I),
        "",
        "Marketing & Advertising",
        "",
    ),
    (
        re.compile(r"\b(insurance|geico|state\s*farm|hiscox|chubb|hartford)\b", re.I),
        "",
        "Insurance",
        "",
    ),
    (
        re.compile(r"\b(clean\w*|janitor\w*|plumb\w*|repair\w*|maint\w*|landscap\w*)\b", re.I),
        "",
        "Repairs & Maintenance",
        "",
    ),
    (
        re.compile(r"\b(legal|law\s+office|cpa|accounting|audit|consulting)\b", re.I),
        "",
        "Professional Services",
        "",
    ),
    (
        re.compile(r"\b(rent|wework|regus|property\s*mgmt|lease)\b", re.I),
        "",
        "Rent & Facilities",
        "",
    ),
]

_PREFIX_PATTERNS = re.compile(
    r"^(?:"
    r"sq\s*\*|"
    r"tst\s*\*|"
    r"sp\s*\*|"
    r"pp\s*\*|"
    r"paypal\s*\*|"
    r"dd\s*\*|"
    r"pos\s+(?:debit|purchase|withdrawal)?\s*|"
    r"ach\s+(?:debit|credit|withdrawal|pmt)?\s*|"
    r"debit\s+card\s+(?:purchase)?\s*|"
    r"checkcard\s+\d{4}\s*|"
    r"crd\s*|"
    r"purchase\s+authorized\s+on\s+\d{2}/\d{2}\s*"
    r")+",
    re.I,
)

_TRAILING_NOISE = re.compile(
    r"(?:"
    r"\*[A-Z0-9]{2,12}|"
    r"#\s*\d{2,8}|"
    r"\b\d{5}(?:-\d{4})?\b|"
    r"\b(?:US|USA|CA|NY|TX|FL|WA|IL|MA|UK|GB)\b\s*$"
    r")",
    re.I,
)


def descriptor_key(raw: str) -> str:
    """Stable normalized cache key for a raw bank statement descriptor."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    cleaned = re.sub(r"\b\d{4,}\b", "", cleaned)
    return " ".join(cleaned.split())[:120]


def clean_merchant_descriptor(raw: str) -> tuple[str, str, str, Decimal]:
    """Deterministically clean messy bank descriptors into (name, category, domain, confidence)."""
    original = " ".join(raw.strip().split())
    if not original:
        return ("Unknown Merchant", "Uncategorized Expense", "", Decimal("0.30"))

    matched_name = ""
    matched_cat = ""
    matched_domain = ""
    for pat, fixed_name, cat, domain in _KNOWN_MERCHANTS:
        m = pat.search(original)
        if m:
            matched_name = fixed_name or m.group(0).strip().title()
            matched_cat = cat
            matched_domain = domain
            break

    stripped = _PREFIX_PATTERNS.sub("", original).strip()
    stripped = re.sub(r"\*[A-Z0-9]{2,14}\b", "", stripped)
    stripped = re.sub(
        r"\b(?:card|acct|ref|auth|id)\s*[:#]?\s*[A-Z0-9-]{3,}\b", "", stripped, flags=re.I
    )
    for _ in range(3):
        next_s = _TRAILING_NOISE.sub("", stripped).strip(" -*/#.,")
        if next_s == stripped:
            break
        stripped = next_s
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()

    if matched_name and matched_name in {
        "Amazon Marketplace",
        "Amazon Web Services",
        "Google Cloud",
        "Microsoft",
    }:
        name = matched_name
        confidence = Decimal("0.96")
    elif stripped:
        name = " ".join(
            w.capitalize() if not w.isupper() or len(w) > 3 else w for w in stripped.split()
        )
        # Friendly title-casing for all-caps merchant names like "BOB CLEANING" -> "Bob Cleaning"
        if stripped.isupper():
            name = stripped.title()
        confidence = Decimal("0.88") if matched_cat else Decimal("0.74")
    else:
        name = matched_name or original[:60].title()
        confidence = Decimal("0.70")

    category = matched_cat or "General Operating Expense"
    return (name[:100], category, matched_domain, confidence)


def brandfetch_logo_url(domain: str) -> str | None:
    """Return optional cosmetic Brandfetch CDN logo URL only when BRANDFETCH_CLIENT_ID is set."""
    client_id = os.environ.get("BRANDFETCH_CLIENT_ID", "").strip()
    if not client_id or not domain:
        return None
    return f"https://cdn.brandfetch.io/{quote(domain, safe='')}?c={quote(client_id, safe='')}"


def build_double_entry_preview(
    *,
    merchant_name: str,
    category: str,
    amount: Decimal,
    bank_account_name: str = "Operating Checking",
    suggested_account_name: str | None = None,
) -> dict[str, Any]:
    """Build a balanced double-entry journal preview for a bank transaction."""
    abs_amt = abs(amount).quantize(Decimal("0.01"))
    target_account = suggested_account_name or category
    if amount < Decimal("0"):
        lines = [
            {
                "account_name": target_account,
                "debit": str(abs_amt),
                "credit": "0.00",
                "memo": f"{merchant_name} ({category})",
            },
            {
                "account_name": bank_account_name,
                "debit": "0.00",
                "credit": str(abs_amt),
                "memo": f"Bank outflow to {merchant_name}",
            },
        ]
    else:
        income_account = suggested_account_name or "Sales Revenue"
        lines = [
            {
                "account_name": bank_account_name,
                "debit": str(abs_amt),
                "credit": "0.00",
                "memo": f"Bank deposit from {merchant_name}",
            },
            {
                "account_name": income_account,
                "debit": "0.00",
                "credit": str(abs_amt),
                "memo": f"{merchant_name} receipt",
            },
        ]
    return {
        "balanced": True,
        "total_debit": str(abs_amt),
        "total_credit": str(abs_amt),
        "lines": lines,
    }


def _enrich_with_llm(
    raw_descriptor: str,
    fallback_name: str,
    fallback_category: str,
    settings: Settings | None,
) -> tuple[str, str, Decimal, str] | None:
    """Use GroqModelAdapter (if configured) to normalize unseen bank descriptors."""
    if settings is None or not getattr(settings, "groq_api_key", None):
        return None
    try:
        import importlib

        ai_models = importlib.import_module("ledgerpilot.ai.models")
        adapter = ai_models.GroqModelAdapter(settings)
        prompt = (
            "Normalize this bank statement descriptor into JSON with keys "
            '{"normalized_name": str, "business_category": str, '
            '"confidence": float, "rationale": str}. '
            f"Descriptor: {raw_descriptor!r}"
        )
        reply = adapter.complete(
            system="You are an expert bookkeeper. Return strictly valid JSON only.",
            user=prompt,
        )
        parsed = json.loads(reply)
        name = str(parsed.get("normalized_name") or fallback_name).strip()[:100]
        cat = str(parsed.get("business_category") or fallback_category).strip()[:80]
        conf = Decimal(str(parsed.get("confidence", "0.85"))).quantize(Decimal("0.01"))
        conf = max(Decimal("0.10"), min(Decimal("0.99"), conf))
        rationale = str(parsed.get("rationale") or "Normalized via LLM merchant classifier.")[:200]
        return (name, cat, conf, rationale)
    except Exception:
        return None


def normalize_merchant(
    db: Session | None,
    business_id: UUID,
    raw_descriptor: str,
    *,
    amount: Decimal = Decimal("-100.00"),
    settings: Settings | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Normalize a raw bank descriptor using alias cache -> deterministic rules -> Groq LLM."""
    key = descriptor_key(raw_descriptor)

    if db is not None and key:
        cached = (
            db.execute(
                text(
                    """
                    SELECT mp.id, mp.normalized_name, mp.business_category, mp.default_account_id,
                           mp.logo_url, mp.source, mp.confidence
                    FROM app.merchant_aliases ma
                    JOIN app.merchant_profiles mp ON mp.id = ma.merchant_profile_id
                    WHERE ma.business_id = :bid AND ma.raw_descriptor = :raw
                    LIMIT 1
                    """
                ),
                {"bid": business_id, "raw": key},
            )
            .mappings()
            .first()
        )
        if cached:
            name = str(cached["normalized_name"])
            cat = str(cached["business_category"])
            return {
                "merchant_profile_id": str(cached["id"]),
                "raw_descriptor": raw_descriptor,
                "normalized_name": name,
                "business_category": cat,
                "default_account_id": str(cached["default_account_id"])
                if cached["default_account_id"]
                else None,
                "logo_url": cached["logo_url"],
                "confidence": str(cached["confidence"]),
                "source": "alias_cache",
                "rationale": "Matched previously learned workspace merchant alias.",
                "journal_preview": build_double_entry_preview(
                    merchant_name=name, category=cat, amount=amount
                ),
            }

    name, category, domain, confidence = clean_merchant_descriptor(raw_descriptor)
    source = "deterministic_rules"
    rationale = "Cleaned bank descriptor prefix/suffixes and matched merchant category rules."

    if use_llm and confidence < Decimal("0.90"):
        llm_res = _enrich_with_llm(raw_descriptor, name, category, settings)
        if llm_res is not None:
            name, category, confidence, rationale = llm_res
            source = "ai_llm"

    logo = brandfetch_logo_url(domain)
    profile_id: str | None = None

    if db is not None and key:
        new_pid = uuid4()
        row = (
            db.execute(
                text(
                    """
                    INSERT INTO app.merchant_profiles
                        (id, business_id, normalized_name, business_category,
                         logo_url, source, confidence)
                    VALUES (:id, :bid, :name, :cat, :logo, :src, :conf)
                    ON CONFLICT (business_id, normalized_name)
                    DO UPDATE SET
                        business_category = EXCLUDED.business_category,
                        confidence = GREATEST(
                            app.merchant_profiles.confidence, EXCLUDED.confidence
                        ),
                        updated_at = now()
                    RETURNING id
                    """
                ),
                {
                    "id": new_pid,
                    "bid": business_id,
                    "name": name,
                    "cat": category,
                    "logo": logo,
                    "src": source,
                    "conf": confidence,
                },
            )
            .mappings()
            .one()
        )
        profile_id = str(row["id"])
        db.execute(
            text(
                """
                INSERT INTO app.merchant_aliases
                    (id, business_id, raw_descriptor, merchant_profile_id)
                VALUES (:id, :bid, :raw, :mpid)
                ON CONFLICT (business_id, raw_descriptor) DO NOTHING
                """
            ),
            {"id": uuid4(), "bid": business_id, "raw": key, "mpid": row["id"]},
        )

    return {
        "merchant_profile_id": profile_id,
        "raw_descriptor": raw_descriptor,
        "normalized_name": name,
        "business_category": category,
        "default_account_id": None,
        "logo_url": logo,
        "confidence": str(confidence),
        "source": source,
        "rationale": rationale,
        "journal_preview": build_double_entry_preview(
            merchant_name=name, category=category, amount=amount
        ),
    }
