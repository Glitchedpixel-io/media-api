# app/repositories/media_repository.py
from __future__ import annotations

from typing import cast

from sqlakeyset import select_page
from sqlalchemy import exists, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import joinedload, selectinload

from app.models import ArtworkORM, AssetORM, AssetTagORM, TitleContentORM
from app.models.asset import filename_extension
from app.models.sort_configs import ASSET_SORT
from app.schemas.enums import EntityTypeEnum, MembershipKind
from app.schemas import (
    AssetCreateInternal,
    AssetListParams,
    AssetRead,
    AssetReadExtended,
    AssetUpdateInternal,
    PaginatedResponse,
)

from ..utils.sorting import apply_ordering
from .base_repository import SQLAlchemyBaseRepository
from .errors import EnumViolation, NotFoundError
from .protocols import MediaRepository


class SQLAlchemyMediaRepository(SQLAlchemyBaseRepository, MediaRepository):
    def create(self, asset: AssetCreateInternal) -> AssetRead:
        asset_orm = AssetORM(**asset.model_dump())
        self.db.add(asset_orm)
        self._safe_commit()
        self.db.refresh(asset_orm)
        return AssetRead.model_validate(asset_orm)

    def get(self, asset_id: int, with_master_asset: bool = True) -> AssetReadExtended | None:
        stmt = select(AssetORM).where(AssetORM.id == asset_id)
        if with_master_asset:
            stmt = stmt.options(joinedload(AssetORM.master_asset))
        asset_orm = self.db.execute(stmt).scalar_one_or_none()
        return AssetReadExtended.model_validate(asset_orm) if asset_orm else None

    def get_by_external_id(self, scheme_id: int, external_id: str) -> AssetRead | None:
        stmt = select(AssetORM).where(
            AssetORM.external_ids.any(scheme_id=scheme_id, external_id=external_id)
        )
        asset_orm = self.db.execute(stmt).scalar_one_or_none()
        return AssetRead.model_validate(asset_orm) if asset_orm else None

    def exists(self, asset_id: int) -> bool:
        return self.db.get(AssetORM, asset_id) is not None

    def path_exists(self, path: str, exclude_asset_id: int | None = None) -> bool:
        stmt = select(AssetORM).where(AssetORM.path == path)
        if exclude_asset_id is not None:
            stmt = stmt.where(AssetORM.id != exclude_asset_id)
        return self.db.execute(stmt).scalar_one_or_none() is not None

    def list_derived_assets(self, asset_id: int) -> list[AssetRead]:
        rows = self.db.scalars(select(AssetORM).where(AssetORM.master_asset_id == asset_id)).all()
        return [AssetRead.model_validate(row) for row in rows]

    def list_paged(self, params: AssetListParams) -> PaginatedResponse[AssetReadExtended]:
        # Base selectable
        stmt = select(AssetORM)

        # Filters
        if params.path_prefix:
            # Written against lower(path) rather than as ILIKE so it matches
            # ix_assets_path_lower. The behaviour is the same -- both are a
            # case-insensitive prefix match -- but ILIKE cannot use any index here,
            # and this form uses one. Changing it back without dropping that index
            # would silently reinstate a sequential scan.
            stmt = stmt.where(func.lower(AssetORM.path).like(f"{params.path_prefix.lower()}%"))
        if params.path_part:
            like_val = f"%{params.path_part}%"
            stmt = stmt.where(AssetORM.path.ilike(like_val))
        if params.created_since:
            stmt = stmt.where(AssetORM.created_at >= params.created_since)
        if params.filename_ext:
            # Written against the extension expression rather than as
            # `filename ILIKE '%.ext'` so it matches ix_assets_filename_ext. The
            # behaviour is the same -- both are a case-insensitive match on the run
            # of characters after the final dot -- but the ILIKE form cannot use
            # this index, and a trigram index serving it would be eight times the
            # size and four times slower. Changing it back without dropping that
            # index would silently reinstate a sequential scan, exactly as for
            # path_prefix above.
            ext = params.filename_ext.lstrip(".").lower()
            stmt = stmt.where(filename_extension(AssetORM.filename) == ext)
        if params.size_min is not None:
            stmt = stmt.where(AssetORM.size >= params.size_min)
        if params.size_max is not None:
            stmt = stmt.where(AssetORM.size <= params.size_max)
        if params.duration_min is not None:
            stmt = stmt.where(AssetORM.duration >= params.duration_min)
        if params.duration_max is not None:
            stmt = stmt.where(AssetORM.duration <= params.duration_max)
        if params.has_artwork is not None:
            # A correlated EXISTS rather than a join: an asset may hold several
            # artworks, and a join would return it once per row and turn `limit` into
            # a cap on artworks rather than on assets. Pinning entity_type and
            # entity_id in this order matches the leading columns of
            # ix_artwork_entity_kind_primary, so both directions are index-covered.
            has_artwork = exists().where(
                ArtworkORM.entity_type == EntityTypeEnum.asset,
                ArtworkORM.entity_id == AssetORM.id,
            )
            stmt = stmt.where(has_artwork if params.has_artwork else ~has_artwork)

        if params.has_intrinsic_parent is not None:
            # "What have I not placed yet?" -- the work queue a library-management
            # screen is built around, and the question this endpoint could not express
            # at all before #177.
            #
            # Intrinsic only, deliberately. An asset appearing in a curated list has
            # been *listed*, not placed, and the two are different states a UI has to
            # be able to tell apart: curated membership is unlimited by design, so
            # counting it would report an asset that lives nowhere as placed the moment
            # anyone dropped it into a collection. It also keeps this filter agreeing
            # with the rest of the API, which follows intrinsic edges only when it
            # walks breadcrumbs, sums `TitleMediaTotals`, or borrows a display image.
            #
            # Correlated EXISTS rather than a join, matching `has_artwork` above: an
            # asset can sit under several titles (the same file under two cuts), and a
            # join would return it once per edge and turn `limit` into a cap on edges.
            # Being a correlated EXISTS rather than `id IN (subquery)` also keeps the
            # `false` direction safe -- `NOT IN` over a subquery yielding a NULL returns
            # no rows at all, which is the trap `titles_resolving_artwork` documents.
            #
            # Served by ix_title_contents_asset_membership, whose leading column is
            # asset_id -- confirmed by EXPLAIN against a populated table, an index-only
            # scan in both directions, not read off the index definition (#94). Nothing
            # indexed asset_id before this: `uq_parent_asset_once` leads with
            # parent_title_id, so it answers "what is under this parent", never "where
            # does this asset live".
            has_home = exists().where(
                TitleContentORM.asset_id == AssetORM.id,
                TitleContentORM.membership == MembershipKind.intrinsic,
            )
            stmt = stmt.where(has_home if params.has_intrinsic_parent else ~has_home)

        # tag filtering
        if params.tag_ids:
            # Parsed here, and a bad value raised as EnumViolation, because that is the
            # one route to a 422 for this endpoint: `params` is built by FastAPI through
            # `Depends()`, where a pydantic validator's error escapes as a 500 rather
            # than being collected into a request-validation response. `get_assets`
            # already maps EnumViolation to 422, which is how an unsupported `sort`
            # field reaches the caller.
            #
            # Unguarded, `int()` raised ValueError straight out of the repository and
            # `?tag_ids=abc` was served as a 500 -- and, being a 500, was recorded as a
            # Logfire issue that `QuietClientErrorRoute` had nothing to work with, since
            # it only converts a status that is already correct (#132). The identical
            # parse on GET /api/titles/ was guarded this way in #131.
            try:
                tags = {
                    int(tag) for tag in (tag.strip() for tag in params.tag_ids.split(",")) if tag
                }
            except ValueError as e:
                raise EnumViolation(
                    f"tag_ids must be a comma-separated list of integers: {params.tag_ids!r}"
                ) from e
            if tags:
                stmt = (
                    stmt.join(AssetTagORM, AssetTagORM.asset_id == AssetORM.id)
                    .where(AssetTagORM.tag_id.in_(tags))
                    .distinct()
                )

        # Apply sorting
        stmt = apply_ordering(stmt, ASSET_SORT, params.sort)

        # Include optional
        if params.include:
            inclusions = [item.strip().lower() for item in params.include.split(",")]
            if "tags" in inclusions:
                stmt = stmt.options(selectinload(AssetORM.tags))
            if "master_asset" in inclusions:
                stmt = stmt.options(selectinload(AssetORM.master_asset))
            # external_ids needs no branch here: it is lazy="selectin" on the model, so it
            # is always eager-loaded. include=external_ids stays accepted and is a no-op.

        # Use the cursor to fetch the required page
        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        # Read out the results
        rows = [row[0] for row in list(page)]
        items = [AssetReadExtended.model_validate(item) for item in rows]

        return PaginatedResponse[AssetReadExtended](
            items=items,
            page=self._page_info(page),
        )

    def update(self, asset_id: int, update: AssetUpdateInternal) -> AssetRead:  # type: ignore
        stmt = select(AssetORM).where(AssetORM.id == asset_id)
        orm = self.db.scalar(stmt)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return AssetRead.model_validate(orm, from_attributes=True)

    def mark_assets_seen(self, ids: list[int]) -> int:
        if not ids:
            return 0
        stmt = (
            update(AssetORM).where(AssetORM.id.in_(ids)).values(last_seen=func.current_timestamp())
        )
        result = cast(CursorResult, self.db.execute(stmt))
        self._safe_commit()
        # result.rowcount may be None on some dialects; fall back to counting
        try:
            count = int(result.rowcount or 0)
        except Exception:
            count = 0
        return count
