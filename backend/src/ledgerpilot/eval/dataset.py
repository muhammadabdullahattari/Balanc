"""Versioned corpus for document understanding, bank matching, and anomaly detection."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

CORPUS_VERSION = "2026.11-v1"

DOCUMENT_EXTRACTION_CASES: list[dict[str, Any]] = [
    {
        "case_id": "doc-en-clean-inv-01",
        "locale": "en_US",
        "orientation": "0_deg_clean",
        "filename": "aws-cloud-invoice-1042.pdf",
        "raw_text": (
            "Vendor: Amazon Web Services Inc\n"
            "Invoice #: INV-1042\n"
            "PO #: PO-9001\n"
            "Invoice Date: 2026-10-14\n"
            "Due Date: 2026-11-13\n"
            "Subtotal: 1,200.00 USD\n"
            "Tax: 96.00 USD\n"
            "Total: 1,296.00 USD\n"
        ),
        "ocr_words": [
            {
                "value": "Amazon",
                "page": 1,
                "left": 0.12,
                "top": 0.08,
                "right": 0.34,
                "bottom": 0.11,
                "confidence": 0.99,
            },
            {
                "value": "INV-1042",
                "page": 1,
                "left": 0.65,
                "top": 0.14,
                "right": 0.82,
                "bottom": 0.17,
                "confidence": 0.98,
            },
            {
                "value": "2026-10-14",
                "page": 1,
                "left": 0.65,
                "top": 0.20,
                "right": 0.84,
                "bottom": 0.23,
                "confidence": 0.99,
            },
            {
                "value": "1,296.00",
                "page": 1,
                "left": 0.72,
                "top": 0.78,
                "right": 0.89,
                "bottom": 0.82,
                "confidence": 0.99,
            },
        ],
        "expected": {
            "document_type": "invoice",
            "vendor_name": "Amazon Web Services Inc",
            "document_number": "INV-1042",
            "purchase_order_number": "PO-9001",
            "document_date": "2026-10-14",
            "currency": "USD",
            "subtotal_amount": "1200.00",
            "tax_amount": "96.00",
            "total_amount": "1296.00",
            "arithmetic_consistent": True,
        },
        "ground_truth_total_box": {"left": 0.72, "top": 0.78, "right": 0.89, "bottom": 0.82},
    },
    {
        "case_id": "doc-fr-vat-bill-02",
        "locale": "fr_FR",
        "orientation": "90_deg_rotated",
        "filename": "ovh-facture-fr-884.pdf",
        "raw_text": (
            "Fournisseur: OVHcloud SAS\n"
            "Facture #: FAC-884\n"
            "Date: 2026-10-21\n"
            "Subtotal: 500.00 EUR\n"
            "TVA: 100.00 EUR\n"
            "Total: 600.00 EUR\n"
        ),
        "ocr_words": [
            {
                "value": "600.00",
                "page": 1,
                "left": 0.68,
                "top": 0.81,
                "right": 0.84,
                "bottom": 0.85,
                "confidence": 0.96,
            },
        ],
        "expected": {
            "document_type": "invoice",
            "vendor_name": "OVHcloud SAS",
            "document_number": "FAC-884",
            "document_date": "2026-10-21",
            "currency": "EUR",
            "subtotal_amount": "500.00",
            "tax_amount": "100.00",
            "total_amount": "600.00",
            "arithmetic_consistent": True,
        },
        "ground_truth_total_box": {"left": 0.68, "top": 0.81, "right": 0.84, "bottom": 0.85},
    },
    {
        "case_id": "doc-ur-utility-bill-03",
        "locale": "ur_PK",
        "orientation": "0_deg_clean",
        "filename": "kelectric-karachi-bill.jpg",
        "raw_text": (
            "Supplier: K-Electric Limited\n"
            "Bill No: KE-77210\n"
            "Date: 2026-11-02\n"
            "Subtotal: 42,000.00 PKR\n"
            "GST: 7,560.00 PKR\n"
            "Total: 49,560.00 PKR\n"
            "روپے بل ادائیگی\n"
        ),
        "ocr_words": [
            {
                "value": "49,560.00",
                "page": 1,
                "left": 0.60,
                "top": 0.74,
                "right": 0.82,
                "bottom": 0.79,
                "confidence": 0.95,
            },
        ],
        "expected": {
            "document_type": "bill",
            "vendor_name": "K-Electric Limited",
            "document_number": "KE-77210",
            "document_date": "2026-11-02",
            "currency": "PKR",
            "subtotal_amount": "42000.00",
            "tax_amount": "7560.00",
            "total_amount": "49560.00",
            "arithmetic_consistent": True,
        },
        "ground_truth_total_box": {"left": 0.60, "top": 0.74, "right": 0.82, "bottom": 0.79},
    },
    {
        "case_id": "doc-thermal-receipt-04",
        "locale": "en_US",
        "orientation": "skewed_thermal_receipt",
        "filename": "fuel-station-receipt.jpg",
        "raw_text": (
            "Merchant: Shell Mobility Station\n"
            "Receipt #: RCP-5519\n"
            "Date: 2026-11-05\n"
            "Subtotal: 80.00 USD\n"
            "Tax: 6.40 USD\n"
            "Total: 86.40 USD\n"
        ),
        "ocr_words": [
            {
                "value": "86.40",
                "page": 1,
                "left": 0.55,
                "top": 0.86,
                "right": 0.74,
                "bottom": 0.90,
                "confidence": 0.94,
            },
        ],
        "expected": {
            "document_type": "receipt",
            "vendor_name": "Shell Mobility Station",
            "document_number": "RCP-5519",
            "document_date": "2026-11-05",
            "currency": "USD",
            "subtotal_amount": "80.00",
            "tax_amount": "6.40",
            "total_amount": "86.40",
            "arithmetic_consistent": True,
        },
        "ground_truth_total_box": {"left": 0.55, "top": 0.86, "right": 0.74, "bottom": 0.90},
    },
    {
        "case_id": "doc-credit-note-05",
        "locale": "de_DE",
        "orientation": "180_deg_upside_down",
        "filename": "hetzner-credit-note.pdf",
        "raw_text": (
            "Vendor: Hetzner Online GmbH\n"
            "Credit Note #: CN-310\n"
            "Date: 2026-11-10\n"
            "Subtotal: 100.00 EUR\n"
            "VAT: 19.00 EUR\n"
            "Total: 119.00 EUR\n"
        ),
        "ocr_words": [
            {
                "value": "119.00",
                "page": 1,
                "left": 0.70,
                "top": 0.75,
                "right": 0.85,
                "bottom": 0.79,
                "confidence": 0.97,
            },
        ],
        "expected": {
            "document_type": "credit_note",
            "vendor_name": "Hetzner Online GmbH",
            "document_number": "CN-310",
            "document_date": "2026-11-10",
            "currency": "EUR",
            "subtotal_amount": "100.00",
            "tax_amount": "19.00",
            "total_amount": "119.00",
            "arithmetic_consistent": True,
        },
        "ground_truth_total_box": {"left": 0.70, "top": 0.75, "right": 0.85, "bottom": 0.79},
    },
    {
        "case_id": "doc-po-variance-bill-06",
        "locale": "en_US",
        "orientation": "0_deg_clean",
        "filename": "packaging-supplier-8pct-over.pdf",
        "raw_text": (
            "Vendor: Apex Packaging Ltd\n"
            "Invoice #: BILL-409\n"
            "PO #: PO-2026-04\n"
            "Date: 2026-11-18\n"
            "Subtotal: 5,000.00 USD\n"
            "Tax: 400.00 USD\n"
            "Total: 5,400.00 USD\n"
        ),
        "ocr_words": [
            {
                "value": "5,400.00",
                "page": 1,
                "left": 0.71,
                "top": 0.80,
                "right": 0.88,
                "bottom": 0.84,
                "confidence": 0.99,
            },
        ],
        "expected": {
            "document_type": "invoice",
            "vendor_name": "Apex Packaging Ltd",
            "document_number": "BILL-409",
            "purchase_order_number": "PO-2026-04",
            "document_date": "2026-11-18",
            "currency": "USD",
            "subtotal_amount": "5000.00",
            "tax_amount": "400.00",
            "total_amount": "5400.00",
            "arithmetic_consistent": True,
        },
        "ground_truth_total_box": {"left": 0.71, "top": 0.80, "right": 0.88, "bottom": 0.84},
    },
]

BANK_MATCHING_CASES: list[dict[str, Any]] = [
    {
        "case_id": "match-3-invoice-bundle-1240",
        "statement_amount": Decimal("1240.00"),
        "statement_currency": "USD",
        "fx_rates": {},
        "candidates": [
            {
                "document_id": "doc-101",
                "number": "INV-101",
                "kind": "invoice",
                "outstanding": Decimal("400.00"),
                "currency": "USD",
                "due_date": "2026-11-01",
                "party_name": "Acme Corp",
            },
            {
                "document_id": "doc-102",
                "number": "INV-102",
                "kind": "invoice",
                "outstanding": Decimal("500.00"),
                "currency": "USD",
                "due_date": "2026-11-05",
                "party_name": "Acme Corp",
            },
            {
                "document_id": "doc-103",
                "number": "INV-103",
                "kind": "invoice",
                "outstanding": Decimal("340.00"),
                "currency": "USD",
                "due_date": "2026-11-10",
                "party_name": "Acme Corp",
            },
            {
                "document_id": "doc-104",
                "number": "INV-104",
                "kind": "invoice",
                "outstanding": Decimal("710.00"),
                "currency": "USD",
                "due_date": "2026-11-12",
                "party_name": "Other Client",
            },
        ],
        "expected_match_kind": "bundled_payment",
        "expected_numbers": ["INV-101", "INV-102", "INV-103"],
    },
    {
        "case_id": "match-processor-fee-deducted",
        "statement_amount": Decimal("971.00"),
        "statement_currency": "USD",
        "fx_rates": {},
        "candidates": [
            {
                "document_id": "doc-205",
                "number": "INV-205",
                "kind": "invoice",
                "outstanding": Decimal("1000.00"),
                "currency": "USD",
                "due_date": "2026-11-18",
                "party_name": "Bright Labs",
            },
        ],
        "expected_match_kind": "fee_deducted",
        "expected_numbers": ["INV-205"],
    },
    {
        "case_id": "match-fx-cross-currency",
        "statement_amount": Decimal("1092.00"),
        "statement_currency": "USD",
        "fx_rates": {"EUR": Decimal("1.08")},
        "candidates": [
            {
                "document_id": "doc-309",
                "number": "INV-309",
                "kind": "invoice",
                "outstanding": Decimal("1000.00"),
                "currency": "EUR",
                "due_date": "2026-11-15",
                "party_name": "Berlin Client GmbH",
            },
        ],
        "expected_match_kind": "fx_settlement",
        "expected_numbers": ["INV-309"],
    },
]

SEQUENCE_GAP_CASES: list[dict[str, Any]] = [
    {
        "case_id": "seq-gap-missing-inv-1044",
        "documents": [
            {"kind": "invoice", "number": "INV-1041"},
            {"kind": "invoice", "number": "INV-1042"},
            {"kind": "invoice", "number": "INV-1043"},
            {"kind": "invoice", "number": "INV-1045"},
            {"kind": "invoice", "number": "INV-1046"},
        ],
        "expected_missing": ["INV-1044"],
    },
    {
        "case_id": "seq-contiguous-no-false-positive",
        "documents": [
            {"kind": "bill", "number": "BILL-201"},
            {"kind": "bill", "number": "BILL-202"},
            {"kind": "bill", "number": "BILL-203"},
            {"kind": "bill", "number": "BILL-204"},
        ],
        "expected_missing": [],
    },
]
