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
            "(false). This is not the same question as whether a title *shows* "
            "artwork: a title with no artwork of its own can still resolve some from "
            "its contents, which `include=display_image` reports per row. To ask that "
            "question instead -- which is almost always the one a browse grid wants -- "
            "use `resolves_display_image`"
        ),
    )
    resolves_display_image: bool | None = Field(
        None,
        description=(
            "Only titles that show an image (true), or only those that show none "
            "(false) -- the holes in the grid. **Not the same question as "
            "`has_artwork`**, and the two will disagree for most titles: "
            "`has_artwork` asks whether a title carries artwork of its own, while "
            "this asks whether `include=display_image` would resolve anything for "
            "it, which includes artwork borrowed from its contents. A title with no "
            "artwork of its own still shows one if any of its episodes or their "
            "assets has one. Borrowing follows intrinsic containment only, so a "
            "curated list resolves an image only if it has been given one directly"
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
            "Comma-separated title type codes, matching any of them: `movie,episode`. Codes "
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

    include: str | None = Field(
        None,
        description=(
            "Comma-separated optional resources to include: `display_image` for the "
            "artwork to show, `counts` for `child_count` and `asset_count`, `totals` for "
            "`total_runtime` and `total_size`. Each is a single extra query for the "
            "whole page. Fields not asked for come back null, which is distinct from "
            "a count of 0"
        ),
    )

    model_config = dict(extra="forbid")
