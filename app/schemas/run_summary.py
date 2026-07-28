# app/schemas/run_summary.py
from typing import Any

from pydantic import Field

from .enums import TransformTypeEnum
from .mixins import IDMixin
from .utc_basemodel import Timestamp, UTCBaseModel


class RunSummaryAttrs(UTCBaseModel):
    worker_name: str = Field(
        ..., title="Worker Name", description="Name of the worker process that ran this job"
    )
    worker_type: str = Field(
        ..., title="Worker Type", description="Type/category of worker that ran this job"
    )
    transform_type: TransformTypeEnum = Field(
        ..., title="Transform Type", description="Type of transformation this run performed"
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


class ScannerRunSummaryAttrs(UTCBaseModel):
    worker_name: str = Field(
        ..., title="Worker Name", description="Name of the worker process that ran the scan"
    )
    worker_type: str = Field(
        ..., title="Worker Type", description="Type/category of worker that ran the scan"
    )
    scan_path: str = Field(..., title="Scan Path", description="Absolute path of the scan")
    relative_to_path: str = Field(
        ...,
        title="Relative To Path",
        description="Path relative to which file paths for the API were calculated",
    )
    started_at: Timestamp = Field(..., title="Started At", description="When the scan started")
    running_time: int = Field(
        ..., title="Running Time (s)", description="Total duration of the scan in seconds"
    )
    dry_run: bool = Field(
        ..., title="Dry Run", description="Indicates if the data refers to a dry run or not"
    )
    total_count: int = Field(
        ...,
        title="Total Count",
        description="Total number of file system objects inspected",
    )
    processed_count: int = Field(
        ...,
        title="Processed Count",
        description="Number of files processed into the data store",
    )
    folder_count: int = Field(..., title="Folder Count", description="Number of folders found")

    excluded_count: int = Field(
        ...,
        title="Excluded Count",
        description="Number of files excluded by configured exclusion policies",
    )
    previously_seen_count: int = Field(
        ...,
        title="Previously Seen Count",
        description="Number of previously catalogued files seen",
    )
    error_count: int = Field(
        ...,
        title="Error Count",
        description="Number of files that could not be processed due to an error",
    )

    api_error_count: int = Field(
        ...,
        title="API Error Count",
        description="Number of files that were processed but which could not be recorded in the API",
    )
    no_metadata_count: int = Field(
        ...,
        title="No Metadata Count",
        description="Number of files for which no media metadata was found",
    )
    unsupported_file_count: int = Field(
        ..., title="Unsupported File Count", description="Number of files of an unsupported type"
    )

    model_config = {"from_attributes": True, "extra": "forbid"}


class ScannerRunSummaryCreatePublic(ScannerRunSummaryAttrs):
    pass


class ScannerRunSummaryCreateInternal(ScannerRunSummaryAttrs):
    pass


class ScannerRunSummaryRead(ScannerRunSummaryAttrs, IDMixin):
    created_at: Timestamp = Field(..., description="When the run summary was created")
