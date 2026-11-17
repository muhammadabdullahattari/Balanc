"""Phase 3 Storage and Data Safety verification.

Covers:
- H4: Streaming uploads, rolling SHA-256, memory bounds, S3 adapter, replica-safe recovery.
- H7: Crash-safe disposal state machine, reconciliation job, worker crash, legal hold races.
"""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from ledgerpilot.config import Settings
from ledgerpilot.documents import storage
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.cleanup import reconcile_disposals
from ledgerpilot.workspace.cleanup_models import CleanupRequest, DisposalIntent


@pytest.fixture
def temp_settings(tmp_path: Path) -> Settings:
    from cryptography.fernet import Fernet

    enc_key = Fernet.generate_key().decode()
    return Settings(
        evidence_storage_root=tmp_path / "evidence",
        evidence_encryption_key=enc_key,
        evidence_max_bytes=1024 * 1024,  # 1 MiB cap
    )


def test_streaming_upload_chunked_encryption_and_integrity(temp_settings: Settings) -> None:
    bid = uuid4()
    eid = uuid4()

    # Generate 256 KiB of dummy payload in 64 KiB chunks
    chunk_size = 64 * 1024
    total_chunks = 4
    raw_payload = b"A" * (chunk_size * total_chunks)

    def stream_generator():
        for i in range(total_chunks):
            yield raw_payload[i * chunk_size : (i + 1) * chunk_size]

    key, digest, size = storage.put_stream_sync(temp_settings, bid, eid, stream_generator())

    assert size == len(raw_payload)
    assert digest == hashlib.sha256(raw_payload).hexdigest()
    assert storage.exists(temp_settings, key)

    # Read target file directly: verify chunked magic prefix is present
    target_path = storage.path(temp_settings, key)
    with target_path.open("rb") as f:
        magic = f.read(len(storage.CHUNKED_MAGIC))
        assert magic == storage.CHUNKED_MAGIC

    # Read back through get_stream: verify streaming decryption
    read_chunks = list(storage.get_stream(temp_settings, key, expected_hash=digest))
    assert b"".join(read_chunks) == raw_payload

    # Full get compatibility helper
    assert storage.get(temp_settings, key, digest) == raw_payload


def test_streaming_upload_enforces_size_cap_without_buffering(temp_settings: Settings) -> None:
    bid = uuid4()
    eid = uuid4()

    # Cap is 1 MiB (1048576 bytes); provide stream exceeding cap
    chunk = b"X" * (512 * 1024)

    def oversized_stream():
        yield chunk  # 512 KiB
        yield chunk  # 1024 KiB (at limit)
        yield chunk  # 1536 KiB (exceeds cap)

    with pytest.raises(DomainError) as exc_info:
        storage.put_stream_sync(temp_settings, bid, eid, oversized_stream(), max_bytes=1024 * 1024)

    assert exc_info.value.code == "ERR_FILE_SIZE"
    # Verify temporary file cleaned up and target never finalized
    key = f"{bid}/{eid}.bin"
    assert not storage.exists(temp_settings, key)


def test_replica_safe_s3_storage_recovery(tmp_path: Path, temp_settings: Settings) -> None:
    """When a file is missing on local replica filesystem, fetch from shared S3 storage."""
    s3_mock = MagicMock()
    s3_settings = temp_settings.model_copy(
        update={
            "evidence_storage_backend": "s3",
            "s3_bucket": "test-evidence-bucket",
            "s3_endpoint_url": "http://127.0.0.1:9000",
        }
    )

    bid = uuid4()
    eid = uuid4()
    content = b"%PDF-1.4 test document content for multi-replica verification"
    expected_digest = hashlib.sha256(content).hexdigest()

    with patch("ledgerpilot.documents.storage.get_s3_client", return_value=s3_mock):
        # 1. Put document: writes locally and uploads to S3
        key, digest = storage.put(s3_settings, bid, eid, content)
        assert digest == expected_digest
        s3_mock.upload_file.assert_called_once()

        local_file = storage.path(s3_settings, key)
        assert local_file.exists()

        # 2. Simulate replica node: local file is missing on this replica
        local_file.unlink()
        assert not local_file.exists()

        # Mock s3_mock.download_file to re-create the encrypted file from a temporary put
        def fake_download_file(bucket, obj_key, filename):
            # Write a valid chunked encrypted file to the destination filename
            temp_bid = uuid4()
            temp_eid = uuid4()
            storage.put(temp_settings, temp_bid, temp_eid, content)
            source = storage.path(temp_settings, f"{temp_bid}/{temp_eid}.bin")
            Path(filename).write_bytes(source.read_bytes())

        s3_mock.download_file.side_effect = fake_download_file

        # 3. Read triggers replica-safe recovery from S3
        fetched = storage.get(s3_settings, key, expected_digest)
        assert fetched == content
        s3_mock.download_file.assert_called_once()
        assert local_file.exists()  # Local replica cache restored

        # 4. Deletion cleans up both local file and S3 object
        storage.delete(s3_settings, key)
        assert not local_file.exists()
        s3_mock.delete_object.assert_called_once_with(Bucket="test-evidence-bucket", Key=key)


def test_h7_crash_reconciliation_between_unlink_and_commit(temp_settings: Settings) -> None:
    """Simulate worker death after storage unlink but before final DB commit.
    Reconciliation job must detect the orphaned unlinked state, verify storage absence,
    and complete the record to restore database consistency.
    """
    bid = uuid4()
    cid = uuid4()
    rid = uuid4()
    key = f"{bid}/{rid}.bin"

    # Setup dummy database session and mock models
    mock_db = MagicMock()

    mock_intent = DisposalIntent(
        id=uuid4(),
        business_id=bid,
        cleanup_request_id=cid,
        resource_id=rid,
        location="local_evidence",
        object_key=key,
        state="unlinked",  # Worker unlinked from storage before dying
    )

    mock_request = CleanupRequest(
        id=cid,
        business_id=bid,
        policy_id=uuid4(),
        resource_id=rid,
        reference="Policy-2026-X",
        created_by=uuid4(),
        status="disposing",  # Left in disposing state by the crashed worker
    )

    # When query runs for pending intents:
    mock_db.scalars.return_value = [mock_intent]
    # When query runs for request and legal hold:
    mock_db.scalar.side_effect = [
        mock_request,  # request_for
        None,          # held() -> No active legal hold
    ]

    reconciled = reconcile_disposals(mock_db, temp_settings, business_id=bid)

    assert len(reconciled) == 1
    assert reconciled[0]["action"] == "completed"
    assert reconciled[0]["reason"] == "restored_consistency"

    # Verify state machine reached terminal verified & completed states
    assert mock_intent.state == "completed"
    assert mock_request.status == "verified"
    assert "Managed payload absent" in mock_request.outcome
    assert mock_db.commit.called


def test_h7_legal_hold_arriving_during_disposal_prevents_file_loss(temp_settings: Settings) -> None:
    """When a legal hold is placed while disposal intent is in-flight (intent_recorded),
    reconciliation (and pre-unlink check) must catch it, roll back the intent,
    and abort disposal so the file remains intact on storage.
    """
    bid = uuid4()
    cid = uuid4()
    rid = uuid4()
    key = f"{bid}/{rid}.bin"

    # Put a real evidence file in storage
    storage.put(temp_settings, bid, rid, b"%PDF-1.4 Legal evidence document")
    assert storage.exists(temp_settings, key)

    mock_db = MagicMock()

    mock_intent = DisposalIntent(
        id=uuid4(),
        business_id=bid,
        cleanup_request_id=cid,
        resource_id=rid,
        location="local_evidence",
        object_key=key,
        state="intent_recorded",  # Intent recorded, but unlinking not yet performed
    )

    mock_request = CleanupRequest(
        id=cid,
        business_id=bid,
        policy_id=uuid4(),
        resource_id=rid,
        reference="Policy-2026-X",
        created_by=uuid4(),
        status="disposing",
    )

    # When query runs:
    mock_db.scalars.return_value = [mock_intent]
    mock_db.scalar.side_effect = [
        mock_request,      # request_for
        uuid4(),           # held() -> Active legal hold found!
    ]

    reconciled = reconcile_disposals(mock_db, temp_settings, business_id=bid)

    assert len(reconciled) == 1
    assert reconciled[0]["action"] == "rolled_back"
    assert reconciled[0]["reason"] == "active_legal_hold"

    # CRITICAL: Intent rolled back, file preserved intact on storage!
    assert mock_intent.state == "rolled_back"
    assert mock_request.status == "approved"
    assert "active legal hold" in mock_request.outcome
    assert storage.exists(temp_settings, key), "File must be preserved intact on hold"
    assert mock_db.commit.called


def test_h7_hold_after_unlink_does_not_falsely_claim_file_intact(
    temp_settings: Settings,
) -> None:
    """If file was already unlinked before legal hold arrived, the record must not claim
    the file is intact.
    """
    bid = uuid4()
    cid = uuid4()
    rid = uuid4()
    key = f"{bid}/{rid}.bin"

    mock_db = MagicMock()

    mock_intent = DisposalIntent(
        id=uuid4(),
        business_id=bid,
        cleanup_request_id=cid,
        resource_id=rid,
        location="local_evidence",
        object_key=key,
        state="unlinked",  # Already physically unlinked before hold was placed
    )

    mock_request = CleanupRequest(
        id=cid,
        business_id=bid,
        policy_id=uuid4(),
        resource_id=rid,
        reference="Policy-2026-X",
        created_by=uuid4(),
        status="disposing",
    )

    mock_db.scalars.return_value = [mock_intent]
    mock_db.scalar.side_effect = [
        mock_request,
        uuid4(),  # Active hold exists
    ]

    reconciled = reconcile_disposals(mock_db, temp_settings, business_id=bid)

    assert len(reconciled) == 1
    assert reconciled[0]["action"] == "completed"
    assert reconciled[0]["reason"] == "hold_after_unlink"

    # Terminal state recorded accurately without false claim of intact payload
    assert mock_intent.state == "completed"
    assert mock_request.status == "verified"
    assert "Payload unlinked prior to legal hold" in mock_request.outcome
    assert mock_db.commit.called
