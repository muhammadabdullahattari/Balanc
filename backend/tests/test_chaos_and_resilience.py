"""G7: Fault-injection & chaos resilience tests across storage, leases, LLMs, and cells."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from ledgerpilot.ai.adapter import GroqModelAdapter
from ledgerpilot.banking.matching import MatchCandidateDoc, find_best_document_match
from ledgerpilot.config import Settings
from ledgerpilot.documents import storage
from ledgerpilot.documents.understanding import extract_structured_document
from ledgerpilot.shared.cell_router import CellRouter
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.migration_linter import lint_migrations_directory


def _test_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=SecretStr(
            "postgresql+psycopg://ledgerpilot_app:synthetic@127.0.0.1:5432/lp_test"
        ),
        session_signing_key=SecretStr("a" * 64),
        evidence_storage_root=tmp_path / "evidence",
        evidence_encryption_key=SecretStr(Fernet.generate_key().decode("ascii")),
    )


def test_chaos_01_storage_corruption_and_missing_blob_rejected(tmp_path: Path) -> None:
    """Drill 01: Corrupted or missing encrypted blobs fail closed with 503 errors."""
    settings = _test_settings(tmp_path)
    bid = uuid4()
    eid = uuid4()
    payload = b"%PDF-1.4\nSupplier Invoice 1240.00\n"
    key, digest = storage.put(settings, bid, eid, payload)
    assert storage.get(settings, key, digest) == payload

    # Inject bit-flip corruption inside ciphertext on disk
    blob_path = storage.path(settings, key)
    raw = bytearray(blob_path.read_bytes())
    raw[-5] ^= 0xFF
    blob_path.write_bytes(bytes(raw))

    with pytest.raises(DomainError) as exc_info:
        storage.get(settings, key, digest)
    assert exc_info.value.status == 503

    # Inject total local blob loss
    blob_path.unlink()
    with pytest.raises(DomainError) as missing_exc:
        storage.get(settings, key, digest)
    assert missing_exc.value.code == "ERR_STORAGE_UNAVAILABLE"


def test_chaos_02_online_migration_linter_enforces_zero_table_locks() -> None:
    """Drill 02 / G4: Every repository migration passes online DDL lock linting."""
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    violations = lint_migrations_directory(migrations_dir)
    assert violations == []


def test_chaos_03_payment_execution_lease_blocks_double_payout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drill 03 / A5: Rows in 'executing' or 'settled_unposted' reject duplicate payouts."""
    from ledgerpilot.workspace.p3_automation import (
        SupplierPaymentApproveInput,
        approve_and_execute_supplier_payment,
    )

    monkeypatch.setattr("ledgerpilot.workspace.p3_automation.require_role", lambda *a, **k: None)

    controls_none = MagicMock()
    controls_none.mappings.return_value.first.return_value = None

    executing_row = MagicMock()
    executing_row.mappings.return_value.first.return_value = {
        "id": uuid4(),
        "state": "executing",
        "lease_expires_at": datetime.now(UTC) + timedelta(minutes=4),
        "provider_reference": "",
        "remittance_advice": "",
    }
    mock_db = MagicMock()
    mock_db.execute.side_effect = [controls_none, executing_row]
    mock_user = MagicMock()
    mock_user.user.id = uuid4()

    with pytest.raises(DomainError) as exc_info:
        approve_and_execute_supplier_payment(
            business_id=uuid4(),
            payment_id=uuid4(),
            payload=SupplierPaymentApproveInput(step_up_code="APPROVE"),
            user=mock_user,
            db=mock_db,
            settings=_test_settings(tmp_path),
        )
    assert exc_info.value.code == "ERR_CONFLICT"
    assert exc_info.value.status == 409

    # Also verify 'settled_unposted' replays idempotently without re-sending money
    settled_unposted_row = MagicMock()
    settled_unposted_row.mappings.return_value.first.return_value = {
        "id": uuid4(),
        "state": "settled_unposted",
        "lease_expires_at": None,
        "provider_reference": "tr_123",
        "remittance_advice": "Paid via Stripe",
    }
    mock_db_2 = MagicMock()
    mock_db_2.execute.side_effect = [controls_none, settled_unposted_row]
    replay = approve_and_execute_supplier_payment(
        business_id=uuid4(),
        payment_id=uuid4(),
        payload=SupplierPaymentApproveInput(step_up_code="APPROVE"),
        user=mock_user,
        db=mock_db_2,
        settings=_test_settings(tmp_path),
    )
    assert replay["idempotent_replay"] is True
    assert replay["state"] == "settled_unposted"


def test_chaos_04_model_provider_outage_graceful_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drill 04: On Groq TimeoutError / 503, extraction and 3-invoice matching succeed."""
    settings = _test_settings(tmp_path).model_copy(
        update={"groq_api_key": SecretStr("gsk_simulated_live_key_for_outage_test")}
    )

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise TimeoutError("Simulated Groq 503 / upstream timeout")

    monkeypatch.setattr("httpx.Client.post", _boom)
    adapter = GroqModelAdapter(settings)
    res = adapter.generate_structured(
        feature_id="AI-01",
        system_prompt="Return JSON",
        user_prompt="Match $1,240",
        fallback_payload={"status": "deterministic_ok"},
    )
    assert res.fallback_used is True
    assert res.structured["status"] == "deterministic_ok"

    extracted = extract_structured_document(
        "Vendor: Cloud Host\nInvoice #: INV-99\nDate: 2026-11-10\n"
        "Subtotal: 100.00 USD\nTax: 20.00 USD\nTotal: 120.00 USD",
        filename="inv-99.pdf",
        default_currency="USD",
        settings=settings,
    )
    assert extracted["total_amount"] == "120.00"
    assert extracted["arithmetic_consistent"] is True

    match = find_best_document_match(
        Decimal("1240.00"),
        [
            MatchCandidateDoc(
                "d1", "INV-1", "invoice", Decimal("400.00"), "USD", "2026-11-01", "Client"
            ),
            MatchCandidateDoc(
                "d2", "INV-2", "invoice", Decimal("500.00"), "USD", "2026-11-02", "Client"
            ),
            MatchCandidateDoc(
                "d3", "INV-3", "invoice", Decimal("340.00"), "USD", "2026-11-03", "Client"
            ),
        ],
        statement_currency="USD",
    )
    assert match is not None
    assert match.match_kind == "bundled_payment"
    assert len(match.components) == 3


def test_chaos_05_cell_residency_lock_and_tenant_crypto_shredding(tmp_path: Path) -> None:
    """Drill 05 / G2 & G3: Residency lock & tenant DEK shredding isolate sibling tenants."""
    settings = _test_settings(tmp_path)
    router = CellRouter(settings)
    eu_ep = router.resolve("eu-central-fra-cell-1")
    assert eu_ep.storage_prefix == "cells/eu-central-fra-cell-1"

    with pytest.raises(DomainError) as lock_exc:
        router.resolve(
            "eu-central-fra-cell-1",
            requested_cell="us-east-va-cell-1",
            residency_locked=True,
        )
    assert lock_exc.value.code == "ERR_RESIDENCY_LOCKED"
    assert lock_exc.value.status == 403

    # Tenant A vs Tenant B envelope encryption isolation
    tenant_a = uuid4()
    tenant_b = uuid4()
    wrapped_a = storage.wrap_tenant_dek(settings, Fernet.generate_key())
    wrapped_b = storage.wrap_tenant_dek(settings, Fernet.generate_key())

    key_a, hash_a = storage.put_tenant_envelope(
        settings,
        tenant_a,
        uuid4(),
        b"Tenant A confidential invoice",
        wrapped_a,
        region_cell="eu-central-fra-cell-1",
    )
    key_b, hash_b = storage.put_tenant_envelope(
        settings,
        tenant_b,
        uuid4(),
        b"Tenant B confidential invoice",
        wrapped_b,
        region_cell="us-east-va-cell-1",
    )

    assert (
        storage.get_tenant_envelope(settings, key_a, wrapped_a, hash_a)
        == b"Tenant A confidential invoice"
    )

    # Shred Tenant A's wrapped DEK -> Tenant A is unrecoverable (410), Tenant B decrypts!
    with pytest.raises(DomainError) as shred_exc:
        storage.get_tenant_envelope(settings, key_a, "", hash_a)
    assert shred_exc.value.code == "ERR_KEY_SHREDDED"
    assert shred_exc.value.status == 410

    # Even attempting to decrypt Tenant A's ciphertext with Tenant B's valid DEK fails closed!
    with pytest.raises(DomainError) as cross_exc:
        storage.get_tenant_envelope(settings, key_a, wrapped_b, hash_a)
    assert cross_exc.value.code == "ERR_KEY_SHREDDED"

    assert (
        storage.get_tenant_envelope(settings, key_b, wrapped_b, hash_b)
        == b"Tenant B confidential invoice"
    )
