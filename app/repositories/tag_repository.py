# app/repositories/tag_repository.py
from typing import cast

from sqlakeyset import select_page
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

# Applied to tags brought into existence by tagging something with a name that had
# no tag yet, so they are distinguishable from ones a person deliberately created.
AUTO_CREATED_DESCRIPTION = "<<auto created>>"
AUTO_CREATED_COLOR = "#000000"


def _normalise(names: list[str]) -> list[str]:
    """Lower-case and de-duplicate names, preserving first-seen order.

    Tag names are stored lower-case (see the validator on ``TagAttrs.name``), so
    matching has to be done on the same form.

    Args:
        names: Raw names as supplied by the caller.

    Returns:
        list[str]: Normalised names, without duplicates.
    """
    return list(dict.fromkeys(name.lower() for name in names))


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

    def get_by_names(self, names: list[str]) -> list[TagRead]:
        """Resolve many names at once.

        Args:
            names: Tag names. Case is normalised; duplicates are collapsed.

        Returns:
            list[TagRead]: The tags that exist, in no particular order. Names with
            no tag are simply absent -- the caller decides whether that is an error.
        """
        unique = _normalise(names)
        if not unique:
            return []
        rows = self.db.scalars(select(TagORM).where(TagORM.name.in_(unique))).all()
        return [TagRead.model_validate(row, from_attributes=True) for row in rows]

    def get_or_create_by_names(self, names: list[str]) -> list[TagRead]:
        """Resolve many names at once, creating whatever is missing.

        One ``INSERT ... ON CONFLICT DO NOTHING`` creates every missing tag, which
        makes this safe against a concurrent request creating the same name: the
        conflict is absorbed rather than raised, and the row the other request
        created is picked up by the read below. Checking for each name and then
        inserting it left a window where the loser of that race was told the tag
        could not be created while it demonstrably existed.

        **This flushes; it does not commit.** The created tags become visible to
        the rest of this transaction and are persisted only when the caller
        commits, so a failure later in the same request rolls them back with
        everything else. ``add_asset_tags``/``add_title_tags`` are that commit.

        Args:
            names: Tag names. Case is normalised; duplicates are collapsed.

        Returns:
            list[TagRead]: Every tag for the requested names, pre-existing or
            newly created.
        """
        unique = _normalise(names)
        if not unique:
            return []

        stmt = (
            pg_insert(TagORM)
            .values(
                [
                    {
                        "name": name,
                        "description": AUTO_CREATED_DESCRIPTION,
                        "color": AUTO_CREATED_COLOR,
                        "parent_id": None,
                    }
                    for name in unique
                ]
            )
            .on_conflict_do_nothing(index_elements=["name"])
        )
        self.db.execute(stmt)
        self.db.flush()
        return self.get_by_names(unique)

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

    def _link_tags(
        self,
        association: type[AssetTagORM] | type[TitleTagORM],
        owner_column: str,
        owner_id: int,
        tag_ids: list[int],
    ) -> list[TagRead]:
        """Link tag ids to one owner row, in a fixed number of queries.

        Does not commit: the caller owns the transaction boundary.

        ``ON CONFLICT DO NOTHING ... RETURNING`` gives exactly the rows this call
        inserted, so "already tagged" needs no separate read and cannot race with a
        concurrent request tagging the same pair.

        Args:
            association: The association model (``AssetTagORM`` / ``TitleTagORM``).
            owner_column: Name of the owning foreign key on that model.
            owner_id: Value for that foreign key.
            tag_ids: Tag ids to link. Unknown ids and duplicates are ignored.

        Returns:
            list[TagRead]: The tags newly linked by this call.
        """
        wanted = list(dict.fromkeys(tag_ids))
        if not wanted:
            return []

        # Unknown ids have always been skipped rather than rejected. Filtering them
        # here keeps that behaviour without a query per id, and without letting a
        # bad id turn into a foreign-key violation that fails the whole request.
        known = set(self.db.scalars(select(TagORM.id).where(TagORM.id.in_(wanted))).all())
        insertable = [tag_id for tag_id in wanted if tag_id in known]
        if not insertable:
            return []

        stmt = (
            pg_insert(association)
            .values([{owner_column: owner_id, "tag_id": tag_id} for tag_id in insertable])
            .on_conflict_do_nothing()
            .returning(association.tag_id)
        )
        inserted = set(self.db.scalars(stmt).all())
        if not inserted:
            return []

        rows = self.db.scalars(select(TagORM).where(TagORM.id.in_(inserted))).all()
        return [TagRead.model_validate(row, from_attributes=True) for row in rows]

    # ============ Asset Tagging ============

    def add_asset_tags(self, asset_id: int, tag_ids: list[int]) -> list[TagRead]:
        """Link tags to an asset, and commit the request's work.

        Returns only the tags this call actually linked, so a repeat request
        reports nothing added rather than claiming to have re-tagged.

        Unknown tag ids are ignored rather than raising, which is the behaviour
        this has always had; they are filtered in one query rather than fetched
        one at a time.

        This is the commit for the whole tagging operation -- including any tags
        ``get_or_create_by_names`` flushed earlier in the same transaction -- so it
        commits even when it links nothing.

        Args:
            asset_id: Asset to tag.
            tag_ids: Tag ids to link. Unknown ids and duplicates are ignored.

        Returns:
            list[TagRead]: The tags newly linked by this call.
        """
        linked = self._link_tags(AssetTagORM, "asset_id", asset_id, tag_ids)
        self._safe_commit()
        return linked

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
        """Link tags to a title, and commit the request's work.

        The asset counterpart carries the full note; this is the same operation
        against ``title_tags``.

        Args:
            title_id: Title to tag.
            tag_ids: Tag ids to link. Unknown ids and duplicates are ignored.

        Returns:
            list[TagRead]: The tags newly linked by this call.
        """
        linked = self._link_tags(TitleTagORM, "title_id", title_id, tag_ids)
        self._safe_commit()
        return linked

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
