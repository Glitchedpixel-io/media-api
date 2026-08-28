# app/schemas/asset_filters.py
from __future__ import annotations

from pydantic import Field, field_validator

from ..utils.paths import to_linux_path
from . import UTCBaseModel, Timestamp
from .api_filters import KeysetPagination


class AssetFilters(UTCBaseModel):
    path_prefix: str | None = Field(None, description="Path starts with")
    path_part: str | None = Field(None, description="Path contains part")
    created_since: Timestamp | None = Field(None, description="Created since ISO datetime")
    filename_ext: str | None = Field(None, description="Filename extension like mp4")
    size_min: int | None = Field(None, ge=0, description="Minimum size bytes")
    size_max: int | None = Field(None, ge=0, description="Maximum size bytes")
    duration_min: float | None = Field(None, ge=0, description="Minimum duration seconds")
    duration_max: float | None = Field(None, ge=0, description="Maximum duration seconds")
    tag_ids: str | None = Field(None, description="List of comma separated tag ids")
    has_artwork: bool | None = Field(
        None,
        description=(
            "Only assets that have artwork of any kind (true), or only those with " "none (false)"
        ),
    )

    @field_validator("size_max")
    @classmethod
    def _size_range(cls, v, info) -> int | None:  # type: ignore
        size_min = info.data.get("size_min")
        if v is not None and size_min is not None and v < size_min:
            raise ValueError("size_max must be >= size_min")
        return v  # type: ignore

    @field_validator("duration_max")
    @classmethod
    def _dur_range(cls, v, info) -> float | None:  # type: ignore
        min = info.data.get("duration_min")
        if v is not None and min is not None and v < min:
            raise ValueError("duration_max must be >= duration_min")
        return v  # type: ignore

    @field_validator("path_prefix", mode="before")
    @classmethod
    def _normalize_path(cls, v):  # type: ignore
        return to_linux_path(v) if v else v


class AssetListParams(KeysetPagination, AssetFilters):
    """All list params for /assets as query params."""

    include: str | None = Field(None, description="Optional linked resources to include")

    model_config = dict(extra="forbid")
