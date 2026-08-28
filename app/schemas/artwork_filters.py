# app/schemas/artwork_filters.py
from __future__ import annotations

from pydantic import BaseModel, Field

from .api_filters import KeysetPagination
from .enums import EntityTypeEnum


class ArtworkFilters(BaseModel):
    entity_type: EntityTypeEnum | None = Field(
        None, description="Only artwork belonging to titles, or only to assets"
    )
    entity_id: int | None = Field(
        None,
        description=(
            "Only artwork belonging to this entity. Pair with entity_type: an id alone "
            "matches both a title and an asset, which are different things"
        ),
    )
    kind: str | None = Field(
        None,
        description="Only artwork of this kind, by code, e.g. poster",
        max_length=32,
    )
    is_primary: bool | None = Field(
        None,
        description=(
            "Only the artwork chosen for its entity and kind (true), or only the "
            "alternatives (false)"
        ),
    )
    missing_dimensions: bool | None = Field(
        None,
        description=(
            "Only artwork with no width or height recorded (true), or only artwork "
            "carrying both (false)"
        ),
    )


class ArtworkListParams(KeysetPagination, ArtworkFilters):
    """All list params for /artwork as query params."""

    model_config = dict(extra="forbid")
