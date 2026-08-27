# app/title_service.py
from typing import Any

from fastapi import HTTPException

from app.repositories import (
    ArtworkKindRepository,
    ArtworkRepository,
    TitleRepository,
    TitleTypeRepository,
)
from app.schemas import (
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

#: The `include=` token that asks for a resolved poster, and the artwork kind it
#: resolves. A kind code rather than a hardcoded id: kinds are a lookup table (#41's
#: lesson), so this has to be resolved at request time.
POSTER_INCLUDE = "poster"


class TitleService:
    def __init__(
        self,
        repository: TitleRepository,
        title_type_repository: TitleTypeRepository,
        artwork_repository: ArtworkRepository,
        artwork_kind_repository: ArtworkKindRepository,
    ) -> None:
        self.repository = repository
        self.title_type_repo = title_type_repository
        self.artwork_repo = artwork_repository
        self.artwork_kind_repo = artwork_kind_repository

    def _wants_poster(self, include: str | None) -> bool:
        """Whether this request asked for a resolved poster.

        Parsed the way the repository parses `include` for tags and references, so
        `include=tags,poster` behaves as a caller would expect.
        """
        if not include:
            return False
        return POSTER_INCLUDE in {item.strip().lower() for item in include.split(",")}

    def _attach_posters(self, titles: list[TitleReadExtended]) -> None:
        """Resolve and attach a poster for each title, in one query for the lot.

        Deliberately one call for the whole page rather than one per title: this is the
        endpoint #49 measured at 14.6s against 263ms once a per-row query crept into
        it, and TestTitleListQueryCount holds the line.

        A missing `poster` kind is not an error. It means nobody has created that kind
        in this database -- a lookup table can be edited -- and a grid rendering
        placeholders is a better answer than a 500.
        """
        if not titles:
            return
        kind = self.artwork_kind_repo.get_by_code(POSTER_INCLUDE)
        if kind is None:
            return
        resolved = self.artwork_repo.resolve_for_titles([t.id for t in titles], kind.id)
        for title in titles:
            title.poster = resolved.get(title.id)

    @translate_repository_errors
    def get_titles(
        self,
        params: TitleListParams,
    ) -> PaginatedResponse[TitleReadExtended]:
        """List titles.

        Decorated so an unsupported `sort` field becomes a 422 rather than escaping
        as a 500: `normalize_sort` raises `EnumViolation`, which this maps.
        """
        page = self.repository.list_paged(params)
        if self._wants_poster(params.include):
            self._attach_posters(page.items)
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
        self._attach_posters([extended])
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
