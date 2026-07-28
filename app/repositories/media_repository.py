# app/repositories/media_repository.py
from __future__ import annotations

from typing import cast

from sqlakeyset import select_page
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import joinedload, selectinload

from app.models import AssetORM, AssetTagORM
from app.models.sort_configs import ASSET_SORT
from app.schemas import (
    AssetCreateInternal,
    AssetListParams,
    AssetRead,
    AssetReadExtended,
    AssetUpdateInternal,
    PageInfo,
    PaginatedResponse,
)

from ..utils.sorting import apply_ordering
from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
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
            stmt = stmt.where(AssetORM.path.ilike(f"{params.path_prefix}%"))
        if params.path_part:
            like_val = f"%{params.path_part}%"
            stmt = stmt.where(AssetORM.path.ilike(like_val))
        if params.created_since:
            stmt = stmt.where(AssetORM.created_at >= params.created_since)
        if params.filename_ext:
            ext = params.filename_ext.lstrip(".")
            stmt = stmt.where(AssetORM.filename.ilike(f"%.{ext}"))
        if params.size_min is not None:
            stmt = stmt.where(AssetORM.size >= params.size_min)
        if params.size_max is not None:
            stmt = stmt.where(AssetORM.size <= params.size_max)
        if params.duration_min is not None:
            stmt = stmt.where(AssetORM.duration >= params.duration_min)
        if params.duration_max is not None:
            stmt = stmt.where(AssetORM.duration <= params.duration_max)

        # tag filtering
        if params.tag_ids:
            # get the list of tags to match, discarding empty tags and duplicates
            tags = set(
                int(tag)
                for tag in [tag.strip().lower() for tag in params.tag_ids.split(",")]
                if tag and len(tag) > 0
            )
            if tags:
                stmt = (
                    stmt.join(AssetTagORM, AssetTagORM.asset_id == AssetORM.id)
                    .where(AssetTagORM.tag_id.in_(tags))
                    .distinct()
                )

        # Total count
        sub_query = stmt.order_by(None).subquery()
        count_stmt = select(func.count()).select_from(sub_query)
        total = self.db.scalar(count_stmt) or 0

        # Apply sorting
        stmt = apply_ordering(stmt, ASSET_SORT, params.sort)

        # Include optional
        if params.include:
            inclusions = [item.strip().lower() for item in params.include.split(",")]
            if "tags" in inclusions:
                stmt = stmt.options(selectinload(AssetORM.tags))
            if "master_asset" in inclusions:
                stmt = stmt.options(selectinload(AssetORM.master_asset))
            if "external_ids" in inclusions:
                stmt = stmt.options(selectinload(AssetORM.external_ids))

        # Use the cursor to fetch the required page
        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        # Read out the results
        rows = [row[0] for row in list(page)]
        items = [AssetReadExtended.model_validate(item) for item in rows]

        return PaginatedResponse[AssetReadExtended](
            items=items,
            page=PageInfo(
                next=self._to_cursor(page.paging.next),
                prev=self._to_cursor(page.paging.previous),
            ),
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
