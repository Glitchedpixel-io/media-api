# app/schemas/title_filters.py
from __future__ import annotations

from pydantic import BaseModel, Field

from .api_filters import KeysetPagination


class TitleFilters(BaseModel):
    name: str | None = Field(None, description="Title name")
    has_artwork: bool | None = Field(
        None,
        description=(
            "Only titles carrying artwork of their own (true), or only those with none "
            "(false). This is not the same question as whether a title *shows* a "
            "poster: a title with no artwork of its own can still resolve one from its "
            "contents, which `include=poster` reports per row"
        ),
    )


class TitleListParams(KeysetPagination, TitleFilters):
    """All list params for /titles as query params."""

    include: str | None = Field(None, description="Optional linked resources to include")

    model_config = dict(extra="forbid")
