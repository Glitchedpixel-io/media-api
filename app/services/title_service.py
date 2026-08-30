# app/title_service.py
from typing import Any

from fastapi import HTTPException

from app.repositories import (
    ArtworkKindRepository,
    ArtworkRepository,
    TitleContentRepository,
    TitleRepository,
    TitleTypeRepository,
)
from app.schemas import (
    ArtworkRead,
    PaginatedResponse,
    TitleCreateInternal,
    TitleCreatePublic,
    TitleListParams,
    TitlePatchPublic,
    TitleRead,
    TitleReadExtended,
    TitleUpdateInternal,
)
from app.services.errors import domain_error_detail, translate_repository_errors

#: The `include=` token that asks for a resolved display image.
#:
#: Named for what it is rather than what we wish it were. It used to be `poster`, and
#: `poster` resolved only the poster kind -- but since #127 established that a poster is
#: portrait, and this repository holds none, a strict poster token resolves nothing for
#: every title. A field called `poster` carrying a 16:9 thumbnail asserts more than the
#: data supports, which is the whole subject of #138; renaming it was cheaper than
#: shipping a label that lies (#152).
DISPLAY_IMAGE_INCLUDE = "display_image"

#: Artwork kinds tried in order when resolving a title's display image.
#:
#: **Kind-major, not depth-major**, and the distinction is worth stating because both
#: are defensible. A title's own thumbnail loses to a child's poster: they depict the
#: same content either way, so the better artwork wins rather than the closer row. Within
#: a single kind the existing rule still holds -- a title's own artwork beats anything
#: beneath it -- so "own artwork wins" survives, scoped to the kind being tried.
#:
#: `logo` and `banner` are deliberately absent: neither is a thing to show in a grid
#: slot, and falling back to one would be worse than the placeholder.
DISPLAY_IMAGE_KINDS = ("poster", "cover_art", "thumbnail", "still", "backdrop")

#: The `include=` token asking how many titles and assets each title directly holds.
COUNTS_INCLUDE = "counts"

#: The `include=` token asking for combined runtime and size beneath each title.
#: Separate from `counts` because it costs a recursive walk rather than a group-by,
#: and the grid needs the counts without paying for the walk.
TOTALS_INCLUDE = "totals"


class TitleService:
    def __init__(
        self,
        repository: TitleRepository,
        title_type_repository: TitleTypeRepository,
        artwork_repository: ArtworkRepository,
        artwork_kind_repository: ArtworkKindRepository,
        title_content_repository: TitleContentRepository,
    ) -> None:
        self.repository = repository
        self.title_type_repo = title_type_repository
        self.artwork_repo = artwork_repository
        self.artwork_kind_repo = artwork_kind_repository
        self.title_content_repo = title_content_repository

    @staticmethod
    def _includes(include: str | None, token: str) -> bool:
        """Whether `include=` carries a given token.

        Parsed the way the repository parses `include` for tags and references, so
        `include=tags,poster` behaves as a caller would expect.
        """
        if not include:
            return False
        return token in {item.strip().lower() for item in include.split(",")}

    def _wants_display_image(self, include: str | None) -> bool:
        """Whether this request asked for a resolved display image."""
        return self._includes(include, DISPLAY_IMAGE_INCLUDE)

    def _attach_counts(self, titles: list[TitleReadExtended]) -> None:
        """Attach direct child and asset counts, in one query for the lot.

        A title absent from the result contains nothing, so it gets an explicit 0
        rather than being left null: null means "not requested" on these fields, and
        conflating the two would leave a caller unable to tell an empty title from an
        unasked question.
        """
        if not titles:
            return
        counts = self.title_content_repo.counts_for_titles([t.id for t in titles])
        for title in titles:
            found = counts.get(title.id)
            title.child_count = found.child_count if found else 0
            title.asset_count = found.asset_count if found else 0

    def _attach_totals(self, titles: list[TitleReadExtended]) -> None:
        """Attach combined runtime and size, in one query for the lot.

        Zero-filled for the same reason as `_attach_counts`.
        """
        if not titles:
            return
        totals = self.title_content_repo.totals_for_titles([t.id for t in titles])
        for title in titles:
            found = totals.get(title.id)
            title.total_runtime = found.total_runtime if found else 0.0
            title.total_size = found.total_size if found else 0

    def _display_image_kind_ids(self) -> list[int]:
        """The display chain, resolved from codes to ids.

        The chain is the service's to define, so the code -> id lookup happens here and
        the repository is handed ids -- the same split the artwork routes use for
        `?kind=`. A code with no row is skipped rather than raising, matching
        `_attach_display_images`: a kind can be edited out of the lookup table, and a
        filter that 500s because of it would be worse than one that ignores it.
        """
        by_code = {kind.code: kind.id for kind in self.artwork_kind_repo.list_all()}
        return [by_code[code] for code in DISPLAY_IMAGE_KINDS if code in by_code]

    def _attach_display_images(self, titles: list[TitleReadExtended]) -> None:
        """Resolve and attach a display image for each title, in a few queries.

        Walks ``DISPLAY_IMAGE_KINDS`` in order, resolving each kind for the titles that
        have not resolved yet, so the id list narrows as it goes and the loop stops as
        soon as every title has something.

        **The query count stays independent of page size**, which is the property that
        matters: one lookup for the kinds plus at most one resolution per kind, never
        one per row. `GET /api/titles/` caps at 500 rows, and a resolution walk
        evaluated per row is #49 again -- 14.6s at the cap against 263ms without it.
        `TestTitleListQueryCount` holds that line and is why this narrows the id list
        rather than resolving every kind for every title.

        The kinds are read in one call rather than looked up per code, so adding a kind
        to the chain costs a resolution rather than a resolution plus a lookup.

        A kind in the chain that no longer exists is skipped, not an error: kinds are a
        lookup table and a row can be edited away, and a grid rendering placeholders is
        a better answer than a 500.
        """
        if not titles:
            return

        by_code = {kind.code: kind.id for kind in self.artwork_kind_repo.list_all()}
        remaining = [title.id for title in titles]
        resolved: dict[int, ArtworkRead] = {}

        for code in DISPLAY_IMAGE_KINDS:
            if not remaining:
                break
            kind_id = by_code.get(code)
            if kind_id is None:
                continue
            found = self.artwork_repo.resolve_for_titles(remaining, kind_id)
            resolved.update(found)
            remaining = [title_id for title_id in remaining if title_id not in found]

        for title in titles:
            title.display_image = resolved.get(title.id)

    @translate_repository_errors
    def get_titles(
        self,
        params: TitleListParams,
    ) -> PaginatedResponse[TitleReadExtended]:
        """List titles.

        Decorated so an unsupported `sort` field becomes a 422 rather than escaping
        as a 500: `normalize_sort` raises `EnumViolation`, which this maps.

        The display chain is resolved to kind ids only when `resolves_display_image`
        asks for it, so an ordinary page does not pay for the lookup (#122).
        """
        kind_ids = (
            self._display_image_kind_ids() if params.resolves_display_image is not None else None
        )
        page = self.repository.list_paged(params, kind_ids)
        if self._wants_display_image(params.include):
            self._attach_display_images(page.items)
        if self._includes(params.include, COUNTS_INCLUDE):
            self._attach_counts(page.items)
        if self._includes(params.include, TOTALS_INCLUDE):
            self._attach_totals(page.items)
        return page

    def get_title(self, title_id: int) -> TitleReadExtended:
        """One title, with its poster already resolved.

        Resolved unconditionally here, unlike the list endpoint where it is opt-in via
        `include=poster`. The only reason `include` exists is the cost of doing work
        per row across a 500-row page, and that reason does not apply to one row: this
        is a single extra query whichever way the caller asks. `AssetORM.external_ids`
        is eager for the same reason -- there is no request that wants it unloaded.
        """
        title = self.repository.get(title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
        extended = TitleReadExtended(**title.model_dump())
        self._attach_display_images([extended])
        # Counts and totals are resolved unconditionally here, for the same reason the
        # poster is: `include` exists to avoid doing work per row across a 500-row
        # page, and one row costs one extra query either way. A detail view is also
        # precisely where the totals are wanted.
        self._attach_counts([extended])
        self._attach_totals([extended])
        return extended

    def get_title_by_external_id(self, scheme_id: int, external_id: str) -> TitleRead:
        asset = self.repository.get_by_external_id(scheme_id, external_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Title not found")
        return asset

    def _resolve_title_type_id(self, code: str) -> int:
        """Resolve a title type code to its ID.

        Title types used to be a Postgres enum, so an unknown value was rejected
        by Pydantic before the request ever reached this layer. ``title_type`` is
        now a plain string, which means this check is the only thing standing
        between an unknown code and a foreign key violation -- and it has to
        keep producing a 422, since that is what callers have always received.

        Args:
            code: The submitted title type code, e.g. ``"movie"``.

        Returns:
            int: The ID of the matching title type.

        Raises:
            HTTPException: 422 if no title type has that code.
        """
        title_type = self.title_type_repo.get_by_code(code)
        if title_type is None:
            raise HTTPException(
                status_code=422,
                detail=domain_error_detail(
                    f"Unknown title type: '{code}'. See GET /api/title_types for valid codes.",
                    "value_error",
                ),
            )
        return title_type.id

    @translate_repository_errors
    def create_title(self, title: TitleCreatePublic) -> TitleRead:
        data: dict[str, Any] = title.model_dump()
        data["title_type_id"] = self._resolve_title_type_id(data.pop("title_type"))
        return self.repository.create(TitleCreateInternal(**data))

    @translate_repository_errors(not_found_message="Title not found")
    def update_title(
        self,
        title_id: int,
        update: TitlePatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> TitleRead:
        """Update a title, translating a submitted type code into its foreign key.

        The public model carries ``title_type`` (a code) while the internal one
        carries ``title_type_id``, so the field has to be translated rather than
        passed through -- ``TitleCreateInternal`` forbids extra fields precisely
        so that forgetting to do so fails loudly instead of silently leaving the
        title's type unchanged.

        Translation is keyed on presence, not truthiness, so that both callers
        keep their existing semantics: PATCH (``exclude_none=True``) omits the
        field and leaves the type alone, while PUT (``exclude_none=False``)
        always carries it, and a PUT that omits ``title_type`` still passes
        ``None`` through to the non-nullable column and gets the same 422 it has
        always returned.

        Args:
            title_id: ID of the title to update.
            update: The submitted partial update.
            exclude_none: Whether ``None`` values should be treated as omitted
                (PATCH semantics) rather than as explicit nulls (PUT).

        Returns:
            TitleRead: The updated title.

        Raises:
            HTTPException: 404 if the title does not exist, or 422 if a
                submitted title type code is unknown.
        """
        data: dict[str, Any] = update.model_dump(exclude_none=exclude_none)  # type: ignore
        if "title_type" in data:
            code = data.pop("title_type")
            data["title_type_id"] = self._resolve_title_type_id(code) if code is not None else None
        return self.repository.update(title_id, TitleUpdateInternal(**data))
