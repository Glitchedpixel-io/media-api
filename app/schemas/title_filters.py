# app/schemas/title_filters.py
from __future__ import annotations

from pydantic import BaseModel, Field

from .api_filters import KeysetPagination


class TitleFilters(BaseModel):
    name: str | None = Field(None, description="Title name")


class TitleListParams(KeysetPagination, TitleFilters):
    """All list params for /titles as query params."""

    include: str | None = Field(None, description="Optional linked resources to include")

    model_config = dict(extra="forbid")
