"""Bounded local worker: claim/commit, scan outside transactions, leased completion.

Run as a trusted local operator with the application role, not through an HTTP
endpoint. The job's original actor must still have an active scoped membership.
"""

import argparse
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from ledgerpilot.config import Settings
from ledgerpilot.database import build_engine, set_context
from ledgerpilot.documents import extraction, storage
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.workspace.models import Evidence


def run_once(settings: Settings, actor_id: UUID, business_id: UUID) -> dict[str, Any]:
    engine = build_engine(settings)
    sessions = sessionmaker(engine, expire_on_commit=False)
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
        status, scanner, error = "retry", None, None
        result: dict[str, Any] = {}
        try:
            content = storage.get(settings, key, digest)
            scanner = storage.scan(content, settings)
            try:
                parsed = extraction.extract(content, media_type, settings)
                result = parsed.model_dump(mode="json")
                status = parsed.status
            except DomainError:
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
        except Exception:
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
        return {
            "processed": True,
            "job_id": str(job_id),
            "completion_accepted": finished,
            "outcome": status,
            "error_code": error,
        }
    finally:
        engine.dispose()


def main() -> None:
    import json

    parser = argparse.ArgumentParser(description="Process one scoped local evidence job")
    parser.add_argument("--actor", required=True, type=UUID)
    parser.add_argument("--business", required=True, type=UUID)
    args = parser.parse_args()
    print(json.dumps(run_once(Settings(), args.actor, args.business)))


if __name__ == "__main__":
    main()
