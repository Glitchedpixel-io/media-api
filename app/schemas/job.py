# app/schemas/job.py
from __future__ import annotations

from pydantic import ConfigDict, Field

from app.schemas import UTCBaseModel, Timestamp


class JobAttrs(UTCBaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    job_key: str = Field(
        ...,
        title="Unique Job Key",
        description="Client-chosen identifier for this background job, used to send heartbeats and mark it completed",
    )


class JobCreatePublic(JobAttrs):
    pass


class JobCreateInternal(JobCreatePublic):
    pass


class JobRead(JobCreateInternal):
    created_at: Timestamp = Field(..., description="When the job was created")
    heartbeat_at: Timestamp | None = Field(None, description="Time of receipt of last heartbeat")
    completed_at: Timestamp | None = Field(None, description="When the job was marked completed")
