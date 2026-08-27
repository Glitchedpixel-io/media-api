# app/schemas/title.py
from __future__ import annotations

from pydantic import BaseModel, Field

from ._dynamic import make_partial_model
from .artwork import ArtworkRead
from .mixins import IDMixin
from .tag import TagRead
from .title_reference import TitleReferenceRead


class TitleAttrs(BaseModel):
    """The public shape of a title, where the type is identified by its code."""

    model_config = {"from_attributes": True, "extra": "forbid"}

    name: str = Field(..., title="Name", description="Name of the title to be used in the UI")
    title_type: str = Field(
        ...,
        title="Title Type",
        description=(
            "Code of the title's type, e.g. movie, tv, or season. Must match the code of an "
            "existing title type; see GET /api/title_types for the available codes."
        ),
        max_length=32,
    )
    release_year: int | None = Field(
        None, title="Release Year", description="Release year of the title"
    )
    synopsis: str | None = Field(None, title="Synopsis", description="Synopsis of the title")


class TitleCreatePublic(TitleAttrs):
    pass


class TitleCreateInternal(BaseModel):
    """The persistence shape of a title, where the type is a foreign key.

    ``extra="forbid"`` is load-bearing rather than decorative: the public models
    carry ``title_type`` (a code) and this one carries ``title_type_id``, so a
    caller that forgets to translate between them gets a loud validation error
    instead of Pydantic silently dropping the field and leaving the title's type
    unchanged. See ``TitleService.update_title``.
    """

    model_config = {"from_attributes": True, "extra": "forbid"}

    name: str = Field(..., title="Name", description="Name of the title to be used in the UI")
    title_type_id: int = Field(
        ..., title="Title Type ID", description="ID of the title's type in title_types"
    )
    release_year: int | None = Field(
        None, title="Release Year", description="Release year of the title"
    )
    synopsis: str | None = Field(None, title="Synopsis", description="Synopsis of the title")


class TitleRead(TitleAttrs, IDMixin):
    pass


class TitleReadExtended(TitleRead):
    poster: ArtworkRead | None = Field(
        None,
        title="Poster",
        description=(
            "The poster to show for this title, resolved from the title's own artwork "
            "or, failing that, borrowed from the first entry of its contents. Null "
            "when the title has none and nothing beneath it does either, and also when "
            "`include=poster` was not requested -- ask for it to tell the two apart."
        ),
    )
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
