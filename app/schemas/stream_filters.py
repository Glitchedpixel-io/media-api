# app/schemas/stream_filters.py
from __future__ import annotations

from pydantic import BaseModel, Field

from .api_filters import KeysetPagination


class StreamFilters(BaseModel):
    asset_id: int | None = Field(None, description="Only streams belonging to this asset")


class StreamListParams(KeysetPagination, StreamFilters):
    """All list params for /streams as query params."""

    model_config = dict(extra="forbid")
