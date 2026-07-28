# app/schemas/api_filters.py
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from .enums import OutcomeEnum, TransformTypeEnum


class KeysetPagination(BaseModel):
    limit: int = Field(50, ge=1, le=500)
    sort: str = Field("id:asc", description="Comma-separated fields, e.g. created_at:desc,id:asc")
    after: str | None = Field(None, description="Opaque cursor for next page")
    before: str | None = Field(None, description="Opaque cursor for previous page")


class PageInfo(BaseModel):
    next: str | None = Field(
        None, description="Opaque cursor for the next page, or null if this is the last page"
    )
    prev: str | None = Field(
        None, description="Opaque cursor for the previous page, or null if this is the first page"
    )


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic container for cursor-paginated lists."""

    items: list[T] = Field(..., description="The page of results")
    page: PageInfo = Field(..., description="Cursors for fetching adjacent pages")


class TransformRequestFilters(BaseModel):
    transform_type: TransformTypeEnum | None = None
    actioned: bool | None = None
    worker_assigned: bool | None = Field(
        None, description="Filter where worker is set (True) or not set (False)"
    )
    outcome: OutcomeEnum | None = None


class TransformRequestListParams(KeysetPagination, TransformRequestFilters):
    """All list params for /transform_requests as query params."""

    model_config = dict(extra="forbid")
    sort: str = Field(
        "created_at:desc", description="Comma-separated fields, e.g. created_at:desc,id:asc"
    )
