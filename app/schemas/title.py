# app/schemas/title.py
from __future__ import annotations

from pydantic import BaseModel, Field

from ._dynamic import make_partial_model
from .enums import TitleTypeEnum
from .mixins import IDMixin
from .tag import TagRead
from .title_reference import TitleReferenceRead


class TitleAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    name: str = Field(..., title="Name", description="Name of the title to be used in the UI")
    title_type: TitleTypeEnum = Field(
        ..., title="Title Type", description="Category of title, e.g. movie, tv, or season"
    )
    release_year: int | None = Field(
        None, title="Release Year", description="Release year of the title"
    )
    synopsis: str | None = Field(None, title="Synopsis", description="Synopsis of the title")


class TitleCreatePublic(TitleAttrs):
    pass


class TitleCreateInternal(TitleCreatePublic):
    pass


class TitleRead(TitleCreateInternal, IDMixin):
    pass


class TitleReadExtended(TitleRead):
    tags: list[TagRead] | None = Field(
        None, title="List of tags", description="Tags applied to this title"
    )
    references: list[TitleReferenceRead] | None = Field(
        None,
        title="Collection of reference material for this title",
        description="External reference material (reviews, articles, etc.) linked to this title",
    )


TitlePatchPublic = make_partial_model(TitleCreatePublic, name="TitlePatchPublic")
TitleUpdateInternal = make_partial_model(TitleCreateInternal, name="TitleUpdateInternal")
