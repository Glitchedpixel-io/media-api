# app/schemas/transform_request.py
from typing import Any

from pydantic import Field

from . import make_partial_model
from .asset import AssetRead
from .enums import OutcomeEnum, TransformTypeEnum
from .mixins import IDMixin
from .utc_basemodel import Timestamp, UTCBaseModel


class TransformRequestAttrs(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    transform_type: TransformTypeEnum = Field(
        ..., title="Transform Type", description="Type of transformation to perform"
    )
    parameters: dict[str, Any] | None = Field(
        None, title="Parameters", description="JSON parameters for the transformation"
    )
    actioned: bool = Field(
        default=False,
        title="Actioned",
        description="Whether the request has been processed",
    )
    processed_at: Timestamp | None = Field(
        None, title="Processed At", description="When the request was processed"
    )
    worker_notes: str | None = Field(
        None, title="Worker Notes", description="Notes from the processing worker"
    )
    external_job_id: str | None = Field(
        None,
        title="External Job ID",
        description="Backend-assigned job reference reported by the worker",
    )
    duration: float | None = Field(
        None,
        title="Duration",
        description="Time taken to complete the transform in seconds",
    )
    outcome: OutcomeEnum | None = Field(
        None, title="Outcome", description="Result of the transformation"
    )
    worker: str | None = Field(
        None,
        title="Worker",
        description="Identifier of the worker that processed the request",
    )
    on_success: dict[str, Any] | None = Field(
        None,
        title="On Success",
        description="Configuration for a follow-on transform request to create automatically if this one succeeds",
    )
    on_failure: dict[str, Any] | None = Field(
        None,
        title="On Failure",
        description="Configuration for a follow-on transform request to create automatically if this one fails",
    )


class TransformRequestCreatePublic(TransformRequestAttrs):
    """
    Create a transform request with only required fields.
    Optional fields can be omitted and will be excluded from the request when exclude_unset=True is used.
    """

    pass


class TransformRequestCreateInternal(TransformRequestCreatePublic):
    asset_id: int = Field(..., title="Asset ID", description="ID of the associated asset")
    parent_transform_request_id: int | None = Field(
        None,
        title="Parent Transform Request ID",
        description="ID of the parent request",
    )


class TransformRequestRead(TransformRequestCreateInternal, IDMixin):
    created_at: Timestamp = Field(..., description="When the request was created")
    first_heartbeat: Timestamp | None = Field(None, description="Time of first heartbeat")
    last_heartbeat: Timestamp | None = Field(None, description="Time of most recent heartbeat")


TransformRequestPatchPublic = make_partial_model(
    TransformRequestCreatePublic, name="TransformRequestPatchPublic"
)

TransformRequestUpdateInternal = make_partial_model(
    TransformRequestCreatePublic, name="TransformRequestUpdateInternal"
)


class TransformRequestReadExpanded(TransformRequestRead):
    # Include the mapped asset object
    asset: AssetRead = Field(
        ..., title="Associated asset", description="The asset this transform request operates on"
    )


class TransformRequestClaim(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    transform_type: TransformTypeEnum = Field(
        ..., title="Transform Type", description="Type of transformation to claim"
    )
    worker: str = Field(
        ..., title="Worker", description="Identifier of the worker claiming the task"
    )
    external_job_id: str | None = Field(
        None,
        title="External Job ID",
        description="Backend-assigned job reference reported by the worker",
    )


class TransformRequestLogEntry(UTCBaseModel):
    """A single normalised log line reported by the transform's execution backend."""

    model_config = {"from_attributes": True, "extra": "forbid"}

    timestamp: str = Field(
        ...,
        title="Timestamp",
        description="When the log line was emitted, as reported by the backend",
    )
    level: str = Field(..., title="Level", description="Log level, e.g. INFO, WARNING, ERROR")
    logger: str | None = Field(
        None, title="Logger", description="Name of the logger that emitted the line, if reported"
    )
    message: str = Field(..., title="Message", description="The log line's text")
    external_ref: str | None = Field(
        None,
        title="External Reference",
        description="Backend-specific identifier this line belongs to, e.g. a Prefect flow run id",
    )
