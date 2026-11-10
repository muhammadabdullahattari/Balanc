import argparse
import json
import logging
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.documents import extraction, storage
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.shared.telemetry import emit
from ledgerpilot.workspace.models import Evidence


def run_once(settings: Settings, actor_id: UUID, business_id: UUID) -> dict[str, Any]:
    engine = build_engine(settings)
    sessions = sessionmaker(engine, expire_on_commit=False)
    job_id: UUID | None = None
    try:
        with sessions.begin() as db:
            set_context(db, actor_id, business_id)
            claim = (
                db.execute(text("SELECT * FROM app.claim_evidence_job(:b)"), {"b": business_id})
                .mappings()
                .one_or_none()
            )
            if claim is None:
                return {"processed": False}
            item = db.scalar(
                select(Evidence).where(
                    Evidence.business_id == business_id,
                    Evidence.id == claim["evidence_id"],
                )
            )
            if item is None:
                raise DomainError("ERR_NOT_FOUND", "Evidence job resource is unavailable.", 404)
            key, digest, media_type = item.object_key, item.content_hash, item.media_type
            job_id, token = claim["id"], claim["lease_token"]
        emit("evidence.job_claimed", user_id=actor_id, business_id=business_id, job_id=job_id)
        started_at = monotonic()
        status, scanner, error = "retry", None, None
        result: dict[str, Any] = {}
        try:
            content = storage.get(settings, key, digest)
            scanner = storage.scan(content, settings)
            try:
                parsed = extraction.extract(content, media_type, settings)
                result = parsed.model_dump(mode="json")
                status = parsed.status
            except DomainError as exc:
                emit(
                    "evidence.extraction_unavailable",
                    level=logging.WARNING,
                    error=exc,
                    code=exc.code,
                    job_id=job_id,
                    business_id=business_id,
                )
                status, error = "unsupported", "ERR_OCR_UNAVAILABLE"
                result = {
                    "status": status,
                    "model_version": None,
                    "fields": [],
                    "raw_text": None,
                    "reason": "OCR is unavailable; manual review is available.",
                    "requires_review": True,
                }
        except DomainError as exc:
            emit(
                "evidence.processing_failed",
                level=logging.WARNING,
                error=exc,
                code=exc.code,
                job_id=job_id,
                business_id=business_id,
            )
            if exc.code == "ERR_MALWARE":
                status = "rejected"
                result = {
                    "status": status,
                    "model_version": None,
                    "fields": [],
                    "raw_text": None,
                    "reason": "Malware rejected.",
                    "requires_review": True,
                }
            else:
                error = exc.code
        except Exception as exc:
            emit(
                "evidence.worker_failed",
                level=logging.ERROR,
                error=exc,
                code="ERR_WORKER",
                job_id=job_id,
                business_id=business_id,
            )
            error = "ERR_WORKER"
        with sessions.begin() as db:
            set_context(db, actor_id, business_id)
            finished = db.scalar(
                text(
                    "SELECT app.complete_evidence_job(:id,:token,:scanner,"
                    "CAST(:result AS jsonb),:status,:error)"
                ),
                {
                    "id": job_id,
                    "token": token,
                    "status": status,
                    "scanner": scanner,
                    "error": error,
                    "result": json.dumps(result),
                },
            )
        emit(
            "evidence.job_completed" if finished else "evidence.lease_rejected",
            level=logging.INFO if finished else logging.WARNING,
            job_id=job_id,
            business_id=business_id,
            stage=status,
            code=error,
            duration_ms=int((monotonic() - started_at) * 1000),
        )
        return {
            "processed": True,
            "job_id": str(job_id),
            "completion_accepted": finished,
            "outcome": status,
            "error_code": error,
        }
    except SQLAlchemyError as exc:
        emit(
            "evidence.database_failed",
            level=logging.ERROR,
            error=exc,
            code="ERR_DEPENDENCY",
            business_id=business_id,
            job_id=job_id,
        )
        # The transaction contexts have rolled back. Leave an existing lease to
        # expire normally; never report completion or blindly replay a commit.
        raise DomainError(
            "ERR_DEPENDENCY", "Document processing could not complete.", 503
        ) from None
    finally:
        engine.dispose()


def main() -> None:
    import json

    parser = argparse.ArgumentParser(description="Process one scoped local evidence job")
    parser.add_argument("--actor", required=True, type=UUID)
    parser.add_argument("--business", required=True, type=UUID)
    args = parser.parse_args()
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    try:
        print(json.dumps(run_once(settings, args.actor, args.business)))
    except DomainError as error:
        print(json.dumps({"error": {"code": error.code, "message": error.message}}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
