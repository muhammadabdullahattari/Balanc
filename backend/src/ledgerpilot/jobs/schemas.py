"""Pydantic schemas for background job status and accepted responses."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    kind: str
    queue: str
    state: str
    attempts: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class JobAcceptedResponse(BaseModel):
    job_id: UUID
    status: str = "accepted"
    status_url: str
