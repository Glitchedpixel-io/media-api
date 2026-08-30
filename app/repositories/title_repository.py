# app/repositories/title_repository.py
from collections.abc import Sequence

from sqlakeyset import select_page
from sqlalchemy import exists, select
from sqlalchemy.orm import contains_eager, selectinload

from app.models import ArtworkORM, TitleContentORM, TitleORM, TitleTagORM, TitleTypeORM
from app.models.sort_configs import TITLE_SORT
from app.schemas.enums import EntityTypeEnum
from app.schemas import (
    PaginatedResponse,
    TitleCreateInternal,
    TitleListParams,
    TitleRead,
    TitleReadExtended,
    TitleUpdateInternal,
)

from ..utils.sorting import apply_ordering
from .artwork_repository import titles_resolving_artwork
from .base_repository import SQLAlchemyBaseRepository
from .errors import EnumViolation, NotFoundError
from .protocols import TitleRepository


class SQLAlchemyTitleRepository(SQLAlchemyBaseRepository, TitleRepository):
    def create(self, title: TitleCreateInternal) -> TitleRead:
        orm = TitleORM(**title.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TitleRead.model_validate(orm)

    def get(self, title_id: int) -> TitleRead | None:
        orm = self.db.get(TitleORM, title_id)
        return TitleRead.model_validate(orm) if orm else None

    def get_by_external_id(self, scheme_id: int, external_id: str) -> TitleRead | None:
        stmt = select(TitleORM).where(
            TitleORM.external_ids.any(scheme_id=scheme_id, external_id=external_id)
        )
        title_orm = self.db.execute(stmt).scalar_one_or_none()
        return TitleRead.model_validate(title_orm) if title_orm else None

    def exists(self, title_id: int) -> bool:
        return self.db.get(TitleORM, title_id) is not None

    def list_paged(
        self,
        params: TitleListParams,
        display_image_kind_ids: Sequence[int] | None = None,
    ) -> PaginatedResponse[TitleReadExtended]:
        """List titles, filtered and keyset-paginated.

        **The query the library grid issues** is
        ``?library_root=true&title_type=movie,episode&tag_ids=<genre>``, paged at the default
        ``sort=id:asc``. Probed as one query rather than only as separate filters (#94):
        a filter that is correct alone and drops rows in combination is the failure that
        reaches production. Against a table seeded to the production shape -- 1,585
        titles, 848 title tags, 544 containment edges -- it plans as a sequential scan of
        `titles` with the tag membership resolved through `ix_title_tags_tag_id`, at
        1.1ms.

        Every multi-valued filter is a correlated ``EXISTS`` rather than a join, so a
        title matching two of the requested tags -- or sitting in three curated lists --
        is returned once without ``DISTINCT``. That is not only tidiness: ``limit`` has
        to stay a cap on titles, and the keyset cursor is computed from the last row of
        the page, so a deduplicated result set would page incorrectly.

        Index coverage is stated from ``EXPLAIN`` against that populated table, never
        from the inventory harness alone; #94 records four occasions where the harness
        reported coverage that did not exist.

        Args:
            params: Filters, sort, and cursor for the page.

        Returns:
            PaginatedResponse[TitleReadExtended]: One page of titles.
        """
        # Base selectable. The join to title_types is what lets TITLE_SORT's
        # `title_type` override (which orders by TitleTypeORM.code) resolve.
        # contains_eager reuses that join to populate TitleORM.type instead of
        # letting the relationship's lazy="joined" emit a second one.
        stmt = select(TitleORM).join(TitleORM.type).options(contains_eager(TitleORM.type))

        if params.name:
            stmt = stmt.where(TitleORM.name.ilike(f"%{params.name}%"))
        if params.has_artwork is not None:
            # Artwork the title holds *itself*, which is not the same question as
            # whether the grid shows it an image -- a title with none of its own can
            # still resolve one from its contents (#110). `resolves_display_image`
            # below is that other question; the two are kept as separate filters
            # rather than one, because both are worth asking and neither implies the
            # other.
            #
            # Correlated EXISTS rather than a join, so a title holding several artworks
            # is still returned once and `limit` stays a cap on titles.
            has_artwork = exists().where(
                ArtworkORM.entity_type == EntityTypeEnum.title,
                ArtworkORM.entity_id == TitleORM.id,
            )
            stmt = stmt.where(has_artwork if params.has_artwork else ~has_artwork)

        if params.resolves_display_image is not None:
            # "Which titles are holes in the grid?" -- the question `has_artwork`
            # looked like it answered and does not (#122). A semi-join against the
            # same walk `include=display_image` resolves with, so the filter and the
            # field agree by construction rather than by being written to match.
            #
            # The kinds arrive already resolved to ids, as on the artwork routes: the
            # display chain is the service's to define, and a repository that reached
            # for it would put a service constant behind a database query.
            #
            # `IN (subquery)` rather than a correlated EXISTS, deliberately. The walk
            # does not depend on the outer row, so this lets the planner evaluate it
            # once for the page instead of once per candidate row -- which is the
            # whole reason the filter is affordable. Written as a correlated EXISTS it
            # would be #49 again.
            # The `false` direction compiles to `NOT IN (subquery)`, which returns no
            # rows *at all* if the subquery ever yields a NULL rather than excluding
            # that row. Every column the walk selects is NOT NULL today, so it cannot;
            # `test_the_two_directions_partition_the_library` is what would catch it
            # if a future change made one nullable, because the two sides would stop
            # summing to the library.
            resolving = titles_resolving_artwork(display_image_kind_ids or [])
            predicate = TitleORM.id.in_(resolving)
            stmt = stmt.where(predicate if params.resolves_display_image else ~predicate)

        if params.library_root is not None:
            # The library grid's every-load filter. Served by `ix_titles_library_root_id`
            # together with the default `id` sort, which is what makes the composite
            # worth having -- see #91.
            stmt = stmt.where(TitleORM.library_root.is_(params.library_root))

        if params.title_type is not None:
            # Exact, case-insensitive match on the code, done by lowercasing the *input*
            # rather than the column. `ilike` or `lower(code)` would defeat
            # `ix_title_types_code` entirely: under this database's en_US.utf8 collation
            # a plain btree serves no case-folded comparison, which is how
            # `assets.path_prefix` came to be declared index-covered and sequentially
            # scanned for months (#60). Codes are stored lowercase, so folding the input
            # is sufficient and keeps the index usable.
            codes = {c.strip().lower() for c in params.title_type.split(",") if c.strip()}
            if codes:
                # Resolved to foreign keys through a subquery rather than filtered on the
                # already-joined `title_types.code`. Both are correct; only this one is
                # indexed. Filtering the joined column makes the planner walk all of
                # `titles` in primary-key order and check each row's type, because
                # `ix_titles_title_type_id` indexes the key and nothing indexes "titles
                # whose type has this code". The harness would call the filter covered
                # either way -- the index it names does exist -- which is the false
                # positive #94 was written about.
                #
                # Measured on a table seeded to the production shape, selecting a
                # narrow type: index scan at cost 13.87 against 82.05, and 0.069ms
                # against 0.394ms.
                stmt = stmt.where(
                    TitleORM.title_type_id.in_(
                        select(TitleTypeORM.id).where(TitleTypeORM.code.in_(codes))
                    )
                )

        if params.tag_ids:
            # Any-of, matching `tag_ids` on GET /api/assets/. Correlated EXISTS rather
            # than that endpoint's join-and-distinct: the semantics are identical, but a
            # title carrying two of the requested tags stays one row without needing
            # DISTINCT, so `limit` remains a cap on titles and the keyset cursor is not
            # computed over a deduplicated set. Served by `ix_title_tags_title_id`.
            # Parsed here, and a bad value raised as EnumViolation, because that is the
            # one route to a 422 for this endpoint: `params` is built by FastAPI through
            # `Depends()`, where a pydantic validator's error escapes as a 500 rather
            # than being collected into a request-validation response. `get_titles` is
            # decorated with `translate_repository_errors` for exactly this, which is
            # how an unsupported `sort` field already reaches the caller as a 422.
            try:
                tags = {int(t) for t in (t.strip() for t in params.tag_ids.split(",")) if t}
            except ValueError as e:
                raise EnumViolation(
                    f"tag_ids must be a comma-separated list of integers: {params.tag_ids!r}"
                ) from e
            if tags:
                stmt = stmt.where(
                    exists().where(
                        TitleTagORM.title_id == TitleORM.id,
                        TitleTagORM.tag_id.in_(tags),
                    )
                )

        if params.parent_id is not None or params.membership is not None:
            # Containment, asked from the child's side. One EXISTS covers all three
            # shapes -- a parent alone, a membership kind alone, or both -- because a
            # title under several curated parents must still come back once.
            contained = exists().where(TitleContentORM.child_title_id == TitleORM.id)
            if params.parent_id is not None:
                contained = contained.where(TitleContentORM.parent_title_id == params.parent_id)
            if params.membership is not None:
                contained = contained.where(TitleContentORM.membership == params.membership)
            stmt = stmt.where(contained)

        # Apply sorting
        stmt = apply_ordering(stmt, TITLE_SORT, params.sort)

        # Include optional
        if params.include:
            inclusions = [item.strip().lower() for item in params.include.split(",")]
            if "tags" in inclusions:
                stmt = stmt.options(selectinload(TitleORM.tags))
            if "references" in inclusions:
                stmt = stmt.options(selectinload(TitleORM.references))

        # Use the cursor to fetch the required page
        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        # Read out the results
        rows = [row[0] for row in list(page)]
        items = [TitleReadExtended.model_validate(item) for item in rows]

        return PaginatedResponse[TitleReadExtended](
            items=items,
            page=self._page_info(page),
        )

    def update(self, title_id: int, update: TitleUpdateInternal) -> TitleRead:  # type: ignore
        stmt = select(TitleORM).where(TitleORM.id == title_id)
        orm = self.db.scalar(stmt)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TitleRead.model_validate(orm, from_attributes=True)
