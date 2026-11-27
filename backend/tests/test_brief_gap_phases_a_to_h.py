"""Unit & offline verification tests covering Phases A through H of the Brief Gap Plan."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ledgerpilot.audit.tools import find_document_sequence_gaps
from ledgerpilot.banking.matching import MatchCandidateDoc, find_best_document_match
from ledgerpilot.documents.ocr_service import installed_languages
from ledgerpilot.documents.understanding import (
    classify_document_type,
    extract_structured_document,
)
from ledgerpilot.eval.run import run_evaluation_suite
from ledgerpilot.workspace.exports import quarter_bounds
from ledgerpilot.workspace.retention import RetentionState


def test_phase_a1_document_sequence_gap_detection() -> None:
    gaps = find_document_sequence_gaps(
        [
            {"kind": "invoice", "number": "INV-1001"},
            {"kind": "invoice", "number": "INV-1002"},
            {"kind": "invoice", "number": "INV-1004"},
            {"kind": "invoice", "number": "INV-1005"},
        ]
    )
    assert len(gaps) == 1
    assert gaps[0]["missing_count"] == 1
    assert "INV-1003" in gaps[0]["missing_numbers"]
    assert (
        find_document_sequence_gaps(
            [
                {"kind": "invoice", "number": "INV-1001"},
                {"kind": "invoice", "number": "INV-1002"},
                {"kind": "invoice", "number": "INV-1003"},
            ]
        )
        == []
    )


def test_phase_a2_multi_invoice_fee_fx_and_partial_matching() -> None:
    cands = [
        MatchCandidateDoc(
            "d10", "INV-10", "invoice", Decimal("400.00"), "USD", "2026-10-01", "Atlas Co"
        ),
        MatchCandidateDoc(
            "d11", "INV-11", "invoice", Decimal("500.00"), "USD", "2026-10-05", "Atlas Co"
        ),
        MatchCandidateDoc(
            "d12", "INV-12", "invoice", Decimal("340.00"), "USD", "2026-10-09", "Atlas Co"
        ),
    ]
    bundle = find_best_document_match(
        Decimal("1240.00"),
        cands,
        statement_currency="USD",
    )
    assert bundle is not None
    assert bundle.match_kind == "bundled_payment"
    assert len(bundle.components) == 3
    assert "covers 3 open" in bundle.explanation

    # Partial payment match
    partial = find_best_document_match(
        Decimal("200.00"),
        cands,
        statement_currency="USD",
    )
    assert partial is not None
    assert partial.match_kind == "partial_settlement"
    assert partial.residual == Decimal("200.00")


def test_phase_a4_ten_year_retention_default() -> None:
    state = RetentionState(policies=[], holds=[])
    assert state.default_years == 10


def test_phase_b1_b2_multilingual_document_understanding_and_arithmetic_check() -> None:
    langs = installed_languages()
    assert isinstance(langs, str) and "eng" in langs

    assert classify_document_type("Credit Note #CN-55 Refund", "cn.pdf") == "credit_note"
    extracted = extract_structured_document(
        "Vendor: Northwind Logistics\nInvoice #: NW-501\nPO #: PO-88\n"
        "Date: 2026-11-12\nSubtotal: 1,000.00 USD\nTax: 80.00 USD\nTotal: 1,080.00 USD",
        filename="nw-501.pdf",
        default_currency="USD",
        ocr_words=[
            {
                "text": "1,080.00",
                "page": 1,
                "left": 0.70,
                "top": 0.80,
                "right": 0.88,
                "bottom": 0.84,
                "confidence": 0.99,
            }
        ],
    )
    assert extracted["vendor_name"] == "Northwind Logistics"
    assert extracted["document_number"] == "NW-501"
    assert extracted["purchase_order_number"] == "PO-88"
    assert extracted["total_amount"] == "1080.00"
    assert extracted["arithmetic_consistent"] is True

    # Tampered total triggers arithmetic_consistent = False
    bad = extract_structured_document(
        "Vendor: Bad Math\nSubtotal: 100.00 USD\nTax: 10.00 USD\nTotal: 150.00 USD",
        filename="bad.pdf",
    )
    assert bad["arithmetic_consistent"] is False
    assert "ARITHMETIC_MISMATCH" in bad["uncertainty_flags"]


def test_phase_f1_quarter_bounds_and_offline_pack_verifier() -> None:
    s, e = quarter_bounds("2026-Q3")
    assert (s, e) == (date(2026, 7, 1), date(2026, 9, 30))

    # Load scripts/verify_pack.py and test valid + tampered archives
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "scripts"))
    import verify_pack  # type: ignore[import-not-found]

    priv = Ed25519PrivateKey.generate()
    pub_hex = (
        priv.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )

    journals_bytes = b"journal_id,date,debit,credit\nj1,2026-07-10,100.00,100.00\n"
    manifest = {
        "business_id": str(uuid4()),
        "quarter": "2026-Q3",
        "start": "2026-07-01",
        "end": "2026-09-30",
        "files": {
            "journals.csv": {
                "sha256": hashlib.sha256(journals_bytes).hexdigest(),
                "size": len(journals_bytes),
            }
        },
        "verification_key": {"algorithm": "Ed25519", "public_key_hex": pub_hex},
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    sig_bytes = priv.sign(manifest_bytes)

    buf = io.BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("journals.csv", journals_bytes)
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("manifest.sig", sig_bytes)

    report = verify_pack.verify_auditor_pack(buf.getvalue(), expected_public_key_hex=pub_hex)
    assert report["valid"] is True
    assert report["verified_file_count"] == 1
    assert report["period"]["quarter"] == "2026-Q3"

    # Tamper journals.csv -> verifier must reject!
    bad_buf = io.BytesIO()
    with ZipFile(bad_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("journals.csv", journals_bytes + b"tampered")
        zf.writestr("manifest.json", manifest_bytes)
        zf.writestr("manifest.sig", sig_bytes)

    try:
        verify_pack.verify_auditor_pack(bad_buf.getvalue(), expected_public_key_hex=pub_hex)
        raise AssertionError("Expected tampered archive to fail verification")
    except ValueError as exc:
        assert "SHA-256 mismatch" in str(exc)


def test_phase_f2_empirical_evaluation_harness_passes_baseline() -> None:
    report = run_evaluation_suite()
    assert report["gate_passed"] is True, report["regressions"]
    assert report["metrics"]["extraction_f1"] >= 0.94
    assert report["metrics"]["bank_match_accuracy"] == 1.0
    assert report["metrics"]["sequence_gap_f1"] == 1.0
