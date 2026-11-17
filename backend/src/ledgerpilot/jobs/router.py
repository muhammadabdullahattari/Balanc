"""Job status API endpoints with strict workspace authorization."""

from uuid import UUID

from fastapi import APIRouter

from ledgerpilot.identity.dependencies import DB, Identity
from ledgerpilot.jobs import service
from ledgerpilot.jobs.schemas import JobResponse
from ledgerpilot.shared.errors import DomainError
from ledgerpilot.tenancy.service import authorize

router = APIRouter(prefix="/businesses/{business_id}/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobResponse)
def get_job_status(
    business_id: UUID,
    job_id: UUID,
    identity: Identity,
    db: DB,
) -> JobResponse:
    """Retrieve the status and outcome of a background job in this workspace."""
    authorize(db, identity, business_id)
    job = service.get_job(db, business_id, job_id)
    if job is None:
        raise DomainError("ERR_NOT_FOUND", "Job not found in this workspace.", 404)
    return JobResponse(
        id=job.id,
        business_id=job.business_id,
        kind=job.kind,
        queue=job.queue,
        state=job.state,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=job.result,
        error=job.error_detail,
    )
