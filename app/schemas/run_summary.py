# app/schemas/run_summary.py
from typing import Any

from pydantic import Field

from .mixins import IDMixin
from .transform_routing import (
    TRANSFORM_ROUTING_KEY_DESCRIPTION,
    TRANSFORM_ROUTING_KEY_EXAMPLES,
    TransformRoutingKey,
)
from .utc_basemodel import Timestamp, UTCBaseModel


class RunSummaryAttrs(UTCBaseModel):
    worker_name: str = Field(
        ..., title="Worker Name", description="Name of the worker process that ran this job"
    )
    worker_type: str = Field(
        ..., title="Worker Type", description="Type/category of worker that ran this job"
    )
    transform_type: TransformRoutingKey = Field(
        ...,
        title="Transform Type",
        description=TRANSFORM_ROUTING_KEY_DESCRIPTION,
        examples=TRANSFORM_ROUTING_KEY_EXAMPLES,
    )
    started_at: Timestamp = Field(..., title="Started At", description="When the run started")
    processed_count: int = Field(
        ..., title="Processed Count", description="Number of items processed during the run"
    )
    success_count: int = Field(
        ..., title="Success Count", description="Number of items processed successfully"
    )
    failed_count: int = Field(
        ..., title="Failed Count", description="Number of items that failed to process"
    )
    running_time: int = Field(
        ..., title="Running Time (s)", description="Total duration of the run in seconds"
    )
    extras: dict[str, Any] | None = Field(
        None, title="Extras", description="Additional worker-specific data about the run"
    )

    model_config = {"from_attributes": True, "extra": "forbid"}


class RunSummaryCreatePublic(RunSummaryAttrs):
    pass


class RunSummaryCreateInternal(RunSummaryAttrs):
    pass


class RunSummaryRead(RunSummaryAttrs, IDMixin):
    created_at: Timestamp = Field(..., description="When the run summary was created")


#: Appended to every field that only a filesystem walk can answer. Such a field
#: is optional rather than required, so `None` reads as "this dimension does not
#: apply to this kind of scan" -- a distinction `0` cannot make, and one no
#: consumer could recover once a scanner had been forced to send a zero.
_NOT_APPLICABLE = " Omitted when the scan is not over a filesystem."


class ScannerRunSummaryAttrs(UTCBaseModel):
    """Counters for one scan pass.

    Only the fields describing the scan itself -- who ran it, when, for how
    long, and how much it took in -- are required, because every scanner can
    answer those whatever it scans. The filesystem-specific counters are
    optional: a scanner over a paginated catalogue or a remote playlist has no
    honest value for `folder_count` or `unsupported_file_count`, and forcing it
    to send `0` writes a row that reads as a measurement rather than an absence
    (media-api#37).

    `extras` is the escape hatch for whatever a scanner does count that this
    shape has no field for, mirroring `RunSummaryAttrs.extras`.
    """

    worker_name: str = Field(
        ..., title="Worker Name", description="Name of the worker process that ran the scan"
    )
    worker_type: str = Field(
        ..., title="Worker Type", description="Type/category of worker that ran the scan"
    )
    scan_path: str | None = Field(
        None, title="Scan Path", description="Absolute path of the scan." + _NOT_APPLICABLE
    )
    relative_to_path: str | None = Field(
        None,
        title="Relative To Path",
        description=(
            "Path relative to which file paths for the API were calculated." + _NOT_APPLICABLE
        ),
    )
    started_at: Timestamp = Field(..., title="Started At", description="When the scan started")
    running_time: int = Field(
        ..., title="Running Time (s)", description="Total duration of the scan in seconds"
    )
    dry_run: bool = Field(
        ..., title="Dry Run", description="Indicates if the data refers to a dry run or not"
    )
    total_count: int | None = Field(
        None,
        title="Total Count",
        description="Total number of file system objects inspected." + _NOT_APPLICABLE,
    )
    processed_count: int = Field(
        ...,
        title="Processed Count",
        description="Number of items processed into the data store",
    )
    folder_count: int | None = Field(
        None,
        title="Folder Count",
        description="Number of folders found." + _NOT_APPLICABLE,
    )

    excluded_count: int | None = Field(
        None,
        title="Excluded Count",
        description=(
            "Number of files excluded by configured exclusion policies." + _NOT_APPLICABLE
        ),
    )
    previously_seen_count: int = Field(
        ...,
        title="Previously Seen Count",
        description="Number of previously catalogued items seen",
    )
    error_count: int | None = Field(
        None,
        title="Error Count",
        description=(
            "Number of files that could not be processed due to an error." + _NOT_APPLICABLE
        ),
    )

    api_error_count: int | None = Field(
        None,
        title="API Error Count",
        description=(
            "Number of files that were processed but which could not be recorded in the API."
            + _NOT_APPLICABLE
        ),
    )
    no_metadata_count: int | None = Field(
        None,
        title="No Metadata Count",
        description=("Number of files for which no media metadata was found." + _NOT_APPLICABLE),
    )
    unsupported_file_count: int | None = Field(
        None,
        title="Unsupported File Count",
        description="Number of files of an unsupported type." + _NOT_APPLICABLE,
    )
    extras: dict[str, Any] | None = Field(
        None, title="Extras", description="Additional worker-specific data about the scan"
    )

    model_config = {"from_attributes": True, "extra": "forbid"}


class ScannerRunSummaryCreatePublic(ScannerRunSummaryAttrs):
    pass


class ScannerRunSummaryCreateInternal(ScannerRunSummaryAttrs):
    pass


class ScannerRunSummaryRead(ScannerRunSummaryAttrs, IDMixin):
    created_at: Timestamp = Field(..., description="When the run summary was created")
