# app/schemas/title_filters.py
from __future__ import annotations

from pydantic import BaseModel, Field

from .api_filters import KeysetPagination
from .enums import MembershipKind


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
    library_root: bool | None = Field(
        None,
        description=(
            "Only titles the library grid should offer as entry points (true), or only "
            "those it should not (false). This is the filter the grid applies on every "
            "load; see #91 for why rootness is stored rather than derived from whether "
            "a title has a parent"
        ),
    )
    title_type: str | None = Field(
        None,
        description=(
            "Comma-separated title type codes, matching any of them: `movie,tv`. Codes "
            "are matched exactly and case-insensitively; see GET /api/title_types for "
            "the available codes. An unknown code matches nothing rather than erroring, "
            "so a grid whose type list is stale degrades to an empty page instead of a "
            "422"
        ),
    )
    tag_ids: str | None = Field(
        None,
        description=(
            "Comma-separated tag ids, matching titles carrying any of them. The same "
            "any-of semantics as `tag_ids` on GET /api/assets/"
        ),
    )
    parent_id: int | None = Field(
        None,
        description=(
            "Only titles contained by this title. Combine with `membership` to ask for "
            "one kind of containment; on its own it matches either kind"
        ),
    )
    membership: MembershipKind | None = Field(
        None,
        description=(
            "Only titles contained by another title under this kind of membership -- "
            "`intrinsic` for titles that have a home, `curated` for titles that appear "
            "in a list. With `parent_id`, it constrains that parent's containment; "
            "without one, it asks whether any such edge exists"
        ),
    )


class TitleListParams(KeysetPagination, TitleFilters):
    """All list params for /titles as query params."""

    include: str | None = Field(None, description="Optional linked resources to include")

    model_config = dict(extra="forbid")
