# app/repositories/tag_repository.py
from typing import cast

from sqlakeyset import select_page
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult

from app.models import AssetTagORM, TagORM, TitleTagORM
from app.models.sort_configs import TAG_SORT
from app.schemas import (
    PaginatedResponse,
    TagCounts,
    TagCreateInternal,
    TagListParams,
    TagRead,
    TagUpdateInternal,
)

from ..utils.sorting import apply_ordering
from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TagRepository


class SQLAlchemyTagRepository(SQLAlchemyBaseRepository, TagRepository):
    """
    Repository implementation for managing tags stored in a SQLAlchemy-backed database.

    This class provides methods to create, retrieve, update, and delete tag records,
    and also supports operations related to associating tags with other entities like
    assets and titles. Additionally, it includes methods for retrieving usage statistics
    and listing tags with optional filtering.

    :ivar db: Database session used for executing queries and managing transactions.
    :type db: Session
    """

    def create(self, tag: TagCreateInternal) -> TagRead:
        orm = TagORM(**tag.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TagRead.model_validate(orm)

    def exists(self, tag_id: int) -> bool:
        return self.db.get(TagORM, tag_id) is not None

    def get(self, tag_id: int) -> TagRead | None:
        orm = self.db.get(TagORM, tag_id)
        return TagRead.model_validate(orm) if orm else None

    def get_by_name(self, name: str) -> TagRead | None:
        """Get a tag by name (case-insensitive)"""
        stmt = select(TagORM).where(TagORM.name == name.lower()).limit(1)
        orm = self.db.scalar(stmt)
        return TagRead.model_validate(orm, from_attributes=True) if orm else None

    def update(self, tag_id: int, update: TagUpdateInternal) -> TagRead:  # type: ignore
        stmt = select(TagORM).where(TagORM.id == tag_id)
        orm = self.db.scalar(stmt)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TagRead.model_validate(orm, from_attributes=True)

    def list_tags(self, parent_id: int | None = None) -> list[TagRead]:
        """List all tags, optionally filtered by parent"""
        stmt = select(TagORM)

        if parent_id is not None:
            stmt = stmt.where(TagORM.parent_id == parent_id)
        else:
            # Only root tags if parent_id not specified
            stmt = stmt.where(TagORM.parent_id.is_(None))

        stmt = stmt.order_by(TagORM.name)
        tags = list(self.db.scalars(stmt).all())

        return [TagRead.model_validate(orm, from_attributes=True) for orm in tags]

    def list_paged(
        self, params: TagListParams, parent_id: int | None
    ) -> PaginatedResponse[TagRead]:
        # Base selectable
        stmt = select(TagORM)

        if params.name:
            stmt = stmt.where(TagORM.name.ilike(f"%{params.name}%"))

        if parent_id is not None:
            stmt = stmt.where(TagORM.parent_id == parent_id)
        elif not params.name:
            # Only root tags if parent_id and name are not specified
            stmt = stmt.where(TagORM.parent_id.is_(None))

        # Apply sorting
        stmt = apply_ordering(stmt, TAG_SORT, params.sort)

        # No optional includes available for tags

        # Use the cursor to fetch the required page
        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        # Read out the results
        rows = [row[0] for row in list(page)]
        items = [TagRead.model_validate(item) for item in rows]

        return PaginatedResponse[TagRead](
            items=items,
            page=self._page_info(page),
        )

    # ============ Asset Tagging ============

    def add_asset_tags(self, asset_id: int, tag_ids: list[int]) -> list[TagRead]:
        """Adds multiple tags to an asset, returns the number tags added (i.e. zero if the asset already has all the supplied tags)"""
        existing_stmt = select(AssetTagORM.tag_id).where(AssetTagORM.asset_id == asset_id)
        existing_tag_ids = set(self.db.scalars(existing_stmt).all())

        tags = []
        for tag_id in tag_ids:
            if tag_id not in existing_tag_ids:
                tag = self.get(tag_id)
                if tag:
                    asset_tag = AssetTagORM(asset_id=asset_id, tag_id=tag_id)
                    self.db.add(asset_tag)
                    tags.append(tag)

        self._safe_commit()
        return [TagRead.model_validate(orm, from_attributes=True) for orm in tags]

    def remove_asset_tag(self, asset_id: int, tag_id: int) -> bool:
        """Remove a specific tag from an asset"""
        stmt = (
            delete(AssetTagORM)
            .where(AssetTagORM.asset_id == asset_id)
            .where(AssetTagORM.tag_id == tag_id)
        )
        result = cast(CursorResult, self.db.execute(stmt))
        self._safe_commit()
        return result.rowcount > 0

    def remove_all_asset_tags(self, asset_id: int) -> int:
        """Remove all tags from an asset. Returns count removed."""
        stmt = delete(AssetTagORM).where(AssetTagORM.asset_id == asset_id)
        result = cast(CursorResult, self.db.execute(stmt))
        return result.rowcount

    def get_asset_tags(self, asset_id: int) -> list[TagRead]:
        """Get all tags for an asset"""
        stmt = (
            select(TagORM)
            .join(AssetTagORM, AssetTagORM.tag_id == TagORM.id)
            .where(AssetTagORM.asset_id == asset_id)
            .order_by(TagORM.name)
        )
        return [
            TagRead.model_validate(orm, from_attributes=True) for orm in self.db.scalars(stmt).all()
        ]

    # ============ Title Tagging ============

    def add_title_tags(self, title_id: int, tag_ids: list[int]) -> list[TagRead]:
        """Adds multiple tags to a title, returns the number tags added (i.e. zero if the title already has all the supplied tags)"""
        existing_stmt = select(TitleTagORM.tag_id).where(TitleTagORM.title_id == title_id)
        existing_tag_ids = set(self.db.scalars(existing_stmt).all())

        tags = []
        for tag_id in tag_ids:
            if tag_id not in existing_tag_ids:
                tag = self.get(tag_id)
                if tag:
                    title_tag = TitleTagORM(title_id=title_id, tag_id=tag_id)
                    self.db.add(title_tag)
                    tags.append(tag)

        self._safe_commit()
        return [TagRead.model_validate(orm, from_attributes=True) for orm in tags]

    def remove_title_tag(self, title_id: int, tag_id: int) -> bool:
        """Remove a specific tag from a title"""
        stmt = (
            delete(TitleTagORM)
            .where(TitleTagORM.title_id == title_id)
            .where(TitleTagORM.tag_id == tag_id)
        )
        result = cast(CursorResult, self.db.execute(stmt))
        self._safe_commit()
        return result.rowcount > 0

    def remove_all_title_tags(self, title_id: int) -> int:
        """Remove all tags from a title. Returns count removed."""
        stmt = delete(TitleTagORM).where(TitleTagORM.title_id == title_id)
        result = cast(CursorResult, self.db.execute(stmt))
        return result.rowcount

    def get_title_tags(self, title_id: int) -> list[TagRead]:
        """Get all tags for a title"""
        stmt = (
            select(TagORM)
            .join(TitleTagORM, TitleTagORM.tag_id == TagORM.id)
            .where(TitleTagORM.title_id == title_id)
            .order_by(TagORM.name)
        )
        return [
            TagRead.model_validate(orm, from_attributes=True) for orm in self.db.scalars(stmt).all()
        ]

    # ============ Counting ============

    def count_tag_assets(self, tag_id: int) -> int:
        """Count assets with a specific tag"""
        stmt = select(func.count()).select_from(AssetTagORM).where(AssetTagORM.tag_id == tag_id)
        return self.db.scalar(stmt) or 0

    def count_tag_titles(self, tag_id: int) -> int:
        """Count titles with a specific tag"""
        stmt = select(func.count()).select_from(TitleTagORM).where(TitleTagORM.tag_id == tag_id)
        return self.db.scalar(stmt) or 0

    def get_tag_usage_stats(self, tag_id: int) -> TagCounts:
        """Get usage statistics for a tag"""
        return TagCounts(
            tag_id=tag_id,
            asset_count=self.count_tag_assets(tag_id),
            title_count=self.count_tag_titles(tag_id),
        )
