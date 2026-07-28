# app/schemas/tag_filters.py
from __future__ import annotations

from pydantic import BaseModel, Field

from .api_filters import KeysetPagination


class TagFilters(BaseModel):
    name: str | None = Field(None, description="Tag name")


class TagListParams(KeysetPagination, TagFilters):
    """All list params for /tags as query params."""

    include: str | None = Field(None, description="Optional linked resources to include")

    model_config = dict(extra="forbid")
