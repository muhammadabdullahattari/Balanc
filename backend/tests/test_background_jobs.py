"""Tests for durable background job foundation, worker lifecycle, and retries."""

from datetime import UTC
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from ledgerpilot.jobs import service
from ledgerpilot.jobs.models import BackgroundJob
from ledgerpilot.jobs.worker import BackgroundWorker, register_handler
from ledgerpilot.shared.errors import DomainError


def test_calculate_backoff_exponential_growth_and_jitter() -> None:
    # Test deterministic jitter
    b1 = service.calculate_backoff(1, base=2.0, jitter=0.5)
    assert b1 == 2.5  # 2.0 * 2^0 + 0.5

    b2 = service.calculate_backoff(2, base=2.0, jitter=0.5)
    assert b2 == 4.5  # 2.0 * 2^1 + 0.5

    b3 = service.calculate_backoff(3, base=2.0, jitter=0.5)
    assert b3 == 8.5  # 2.0 * 2^2 + 0.5

    b4 = service.calculate_backoff(4, base=2.0, jitter=0.5)
    assert b4 == 16.5  # 2.0 * 2^3 + 0.5

    # Test capping
    b_cap = service.calculate_backoff(15, base=2.0, max_backoff=60.0, jitter=0.0)
    assert b_cap == 60.0

    # Test randomized jitter falls within expected window
    b_rand = service.calculate_backoff(1, base=2.0)
    assert 2.1 <= b_rand <= 4.0


def test_enqueue_job_and_idempotency() -> None:
    db = MagicMock(spec=Session)
    bid = uuid4()
    actor = uuid4()

    # Case 1: First enqueue with idempotency key
    db.scalar.return_value = None  # No existing job
    job1 = service.enqueue(
        db,
        business_id=bid,
        created_by=actor,
        kind="test_task",
        payload={"foo": "bar"},
        queue="default",
        idempotency_key="unique-task-1",
        max_attempts=3,
        timeout_seconds=60,
    )
    assert job1.business_id == bid
    assert job1.kind == "test_task"
    assert job1.idempotency_key == "unique-task-1"
    assert job1.state == "queued"
    assert job1.max_attempts == 3
    db.add.assert_called_once_with(job1)
    db.flush.assert_called_once()

    # Case 2: Subsequent enqueue with identical idempotency key returns existing job
    db.reset_mock()
    db.scalar.return_value = job1
    job2 = service.enqueue(
        db,
        business_id=bid,
        created_by=actor,
        kind="test_task",
        payload={"foo": "bar_different"},
        queue="default",
        idempotency_key="unique-task-1",
    )
    assert job2 == job1
    db.add.assert_not_called()


def test_claim_job_concurrency_and_lease() -> None:
    db = MagicMock(spec=Session)
    token = uuid4()
    job_id = uuid4()
    bid = uuid4()
    actor = uuid4()

    mock_row = {
        "id": job_id,
        "business_id": bid,
        "created_by": actor,
        "queue": "default",
        "kind": "quickbooks_import",
        "payload": {"cutover": "2026-01-01"},
        "attempts": 1,
        "max_attempts": 5,
        "timeout_seconds": 300,
    }
    db.execute.return_value.mappings.return_value.first.return_value = mock_row

    claimed = service.claim_job(db, "default", token, lease_seconds=120, concurrency_limit=2)
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["kind"] == "quickbooks_import"

    # Concurrency limit reached or no jobs available
    db.execute.return_value.mappings.return_value.first.return_value = None
    none_claimed = service.claim_job(db, "default", token, lease_seconds=120, concurrency_limit=2)
    assert none_claimed is None


def test_finish_and_fail_job_transitions() -> None:
    db = MagicMock(spec=Session)
    token = uuid4()
    job_id = uuid4()

    # Finish job
    db.execute.return_value.scalar.return_value = True
    ok = service.finish_job(db, job_id, token, result={"imported": 42})
    assert ok is True

    # Fail job with remaining attempts -> transitions to 'queued' with backoff
    db.execute.return_value.scalar.return_value = "queued"
    state1 = service.fail_job(
        db,
        job_id,
        token,
        "ERR_NETWORK",
        "Connection reset",
        attempts=2,
        backoff_base=2.0,
    )
    assert state1 == "queued"

    # Fail job exceeding max attempts -> transitions to 'dead_letter'
    db.execute.return_value.scalar.return_value = "dead_letter"
    state2 = service.fail_job(
        db,
        job_id,
        token,
        "ERR_FATAL",
        "Persistent failure",
        attempts=5,
        backoff_base=2.0,
    )
    assert state2 == "dead_letter"


def test_replay_dead_letter_job() -> None:
    db = MagicMock(spec=Session)
    job_id = uuid4()

    db.execute.return_value.scalar.return_value = True
    ok = service.replay_job(db, job_id)
    assert ok is True

    db.execute.return_value.scalar.return_value = False
    not_ok = service.replay_job(db, job_id)
    assert not_ok is False


def test_job_status_endpoint_and_workspace_authorization() -> None:

    from datetime import datetime

    bid = uuid4()
    job_id = uuid4()
    actor = uuid4()

    mock_job = BackgroundJob(
        id=job_id,
        business_id=bid,
        created_by=actor,
        queue="reports",
        kind="report_export",
        state="succeeded",
        attempts=1,
        max_attempts=3,
        created_at=datetime.now(UTC),
        result={"download_url": "https://storage.local/report.csv"},
    )

    db = MagicMock()
    identity = MagicMock()
    identity.user.id = actor

    with (
        patch("ledgerpilot.jobs.router.authorize") as mock_auth,
        patch("ledgerpilot.jobs.service.get_job", return_value=mock_job),
    ):
        from ledgerpilot.jobs.router import get_job_status

        resp = get_job_status(bid, job_id, identity, db)
        assert resp.id == job_id
        assert resp.state == "succeeded"
        assert resp.result == {"download_url": "https://storage.local/report.csv"}
        mock_auth.assert_called_once_with(db, identity, bid)

    # Cross-tenant access: authorize raises ERR_FORBIDDEN or job is not in workspace
    err = DomainError("ERR_FORBIDDEN", "Forbidden", 403)
    with patch("ledgerpilot.jobs.router.authorize", side_effect=err):
        with pytest.raises(DomainError) as exc_info:
            get_job_status(bid, job_id, identity, db)
        assert exc_info.value.code == "ERR_FORBIDDEN"

    # Job not found in tenant's workspace
    with (
        patch("ledgerpilot.jobs.router.authorize"),
        patch("ledgerpilot.jobs.service.get_job", return_value=None),
    ):
        with pytest.raises(DomainError) as exc_info:
            get_job_status(bid, job_id, identity, db)
        assert exc_info.value.code == "ERR_NOT_FOUND"


def test_worker_process_one_and_handler_execution() -> None:
    settings = MagicMock()
    mock_engine = MagicMock()
    worker = BackgroundWorker(settings, engine=mock_engine, queues=["default"], concurrency=2)

    bid = uuid4()
    actor = uuid4()
    job_id = uuid4()

    # Register test handler
    executed = []

    def handle_test(db: Session, b_id: UUID, a_id: UUID, payload: dict) -> dict:
        executed.append((b_id, a_id, payload))
        return {"status": "done", "records": 10}

    register_handler("worker_unit_test_task", handle_test)

    claimed_job = {
        "id": job_id,
        "business_id": bid,
        "created_by": actor,
        "queue": "default",
        "kind": "worker_unit_test_task",
        "payload": {"batch_id": "abc"},
        "attempts": 1,
        "timeout_seconds": 10,
    }

    with (
        patch("ledgerpilot.jobs.service.claim_job", return_value=claimed_job),
        patch("ledgerpilot.jobs.service.finish_job") as mock_finish,
        patch("ledgerpilot.jobs.worker.Session") as mock_session_cls,
        patch("ledgerpilot.jobs.worker.set_context") as mock_set_context,
    ):
        mock_sess = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_sess

        acted = worker.process_one("default")
        assert acted is True
        assert len(executed) == 1
        assert executed[0] == (bid, actor, {"batch_id": "abc"})
        mock_finish.assert_called_once()
        mock_set_context.assert_called_once_with(mock_sess, actor, bid)

    worker.stop()
