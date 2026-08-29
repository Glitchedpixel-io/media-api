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
            "Code of the title's type, e.g. movie, episode, or season. Must match the code of an "
            "existing title type; see GET /api/title_types for the available codes."
        ),
        max_length=32,
    )
    release_year: int | None = Field(
        None, title="Release Year", description="Release year of the title"
    )
    synopsis: str | None = Field(None, title="Synopsis", description="Synopsis of the title")
    library_root: bool = Field(
        False,
        title="Library Root",
        description=(
            "Whether the library grid should offer this title as an entry point. "
            "Independent of whether it is watchable: a series is a root but is not "
            "watchable, an episode is watchable but is not a root, and a season is "
            "neither. Defaults to false, so a title is not offered until something "
            "says it should be."
        ),
    )


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
    library_root: bool = Field(
        False,
        title="Library Root",
        description=(
            "Whether the library grid should offer this title as an entry point. "
            "Independent of whether it is watchable: a series is a root but is not "
            "watchable, an episode is watchable but is not a root, and a season is "
            "neither. Defaults to false, so a title is not offered until something "
            "says it should be."
        ),
    )


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
    child_count: int | None = Field(
        None,
        title="Child Title Count",
        description=(
            "How many titles this title directly contains. Counts every containment "
            "edge regardless of membership, so a curated list reports its real size. "
            "Null when `include=counts` was not requested -- distinct from 0, which "
            "means the title genuinely contains no titles."
        ),
    )
    asset_count: int | None = Field(
        None,
        title="Asset Count",
        description=(
            "How many assets this title directly contains, counted the same way as "
            "`child_count`. Null when `include=counts` was not requested."
        ),
    )
    total_runtime: float | None = Field(
        None,
        title="Total Runtime",
        description=(
            "Combined duration in seconds of every distinct asset beneath this title, "
            "following intrinsic containment only. Null when `include=totals` was not "
            "requested."
        ),
    )
    total_size: int | None = Field(
        None,
        title="Total Size",
        description=(
            "Combined size in bytes of every distinct asset beneath this title, "
            "counted the same way as `total_runtime`. Null when `include=totals` was "
            "not requested."
        ),
    )


TitlePatchPublic = make_partial_model(TitleCreatePublic, name="TitlePatchPublic")
TitleUpdateInternal = make_partial_model(TitleCreateInternal, name="TitleUpdateInternal")
