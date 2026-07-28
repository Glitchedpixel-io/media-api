# app/repositories/title_content_repository.py

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.models import TitleContentORM, TitleORM
from app.schemas import (
    TitleContentCreateInternal,
    TitleContentInsert,
    TitleContentRead,
    TitleContentReadExtended,
    TitleContentReadParent,
    TitleContentUpdateInternal,
)
from app.utils.order_key import DIGITS, between, head, tail

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TitleContentRepository


class SQLAlchemyTitleContentRepository(SQLAlchemyBaseRepository, TitleContentRepository):
    def create(self, title_content: TitleContentCreateInternal) -> TitleContentRead:
        orm = TitleContentORM(**title_content.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TitleContentRead.model_validate(orm)

    def get(self, title_content_id: int) -> TitleContentRead | None:
        orm = self.db.get(TitleContentORM, title_content_id)
        return TitleContentRead.model_validate(orm) if orm else None

    def exists(self, title_content_id: int) -> bool:
        return self.db.get(TitleContentORM, title_content_id) is not None

    def list_title_content(
        self, parent_title_id: int, include_children: bool = False
    ) -> list[TitleContentReadExtended]:
        stmt = (
            select(TitleContentORM)
            .where(TitleContentORM.parent_title_id == parent_title_id)
            .order_by(TitleContentORM.order_key)
        )
        if include_children:
            stmt = stmt.options(selectinload(TitleContentORM.asset)).options(
                selectinload(TitleContentORM.child_title)
            )
        rows = self.db.scalars(stmt).all()
        return [TitleContentReadExtended.model_validate(row) for row in rows]

    def update(
        self,
        title_content_id: int,
        update: TitleContentUpdateInternal,  # type: ignore
        exclude_none: bool = True,
    ) -> TitleContentRead:
        orm = self.db.get(TitleContentORM, title_content_id)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TitleContentRead.model_validate(orm, from_attributes=True)

    def delete_title_content(self, title_content_id: int) -> None:
        stmt = delete(TitleContentORM).where(TitleContentORM.id == title_content_id)
        self.db.execute(stmt)
        self._safe_commit()

    def get_titles_with_asset(self, asset_id: int) -> list[TitleContentReadParent]:
        """
        Fetches and returns a list of parent title content records associated
        with the provided asset ID. The function queries the database for title
        content records that correspond to the given asset ID and processes
        them into a list of objects.

        :param asset_id: The ID of the asset to filter title content records.
        :type asset_id: int
        :return: A list of parent title content objects mapped from the query result.
        :rtype: list[TitleContentReadParent]
        """
        stmt = (
            select(TitleContentORM)
            .join(TitleContentORM.parent_title)
            .where(TitleContentORM.asset_id == asset_id)
            .options(selectinload(TitleContentORM.parent_title))
            .order_by(TitleORM.name.asc())
        )
        rows = self.db.scalars(stmt).all()
        return [TitleContentReadParent.model_validate(row) for row in rows]

    # ---- Ordering helpers -------------------------------------------------
    def _get_order_key(self, content_id: int) -> str | None:
        row = self.db.get(TitleContentORM, content_id)
        return row.order_key if row else None

    def _get_prev_next_keys(
        self,
        parent_title_id: int,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        position: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Compute neighbor keys for positioning under a parent.

        Returns (prev_key, next_key) where new key must satisfy prev < key < next.
        """
        if position == "start":
            # before first
            first = self.db.scalars(
                select(TitleContentORM)
                .where(TitleContentORM.parent_title_id == parent_title_id)
                .order_by(TitleContentORM.order_key.asc())
                .limit(1)
            ).first()
            return (None, first.order_key if first else None)
        if position == "end":
            last = self.db.scalars(
                select(TitleContentORM)
                .where(TitleContentORM.parent_title_id == parent_title_id)
                .order_by(TitleContentORM.order_key.desc())
                .limit(1)
            ).first()
            return (last.order_key if last else None, None)
        if before_id is not None:
            next_key = self._get_order_key(before_id)
            # prev is the immediate predecessor of before_id within the same parent
            prev_row = self.db.scalars(
                select(TitleContentORM)
                .where(TitleContentORM.parent_title_id == parent_title_id)
                .where(TitleContentORM.order_key < next_key)
                .order_by(TitleContentORM.order_key.desc())
                .limit(1)
            ).first()
            return (prev_row.order_key if prev_row else None, next_key)
        if after_id is not None:
            prev_key = self._get_order_key(after_id)
            next_row = self.db.scalars(
                select(TitleContentORM)
                .where(TitleContentORM.parent_title_id == parent_title_id)
                .where(TitleContentORM.order_key > prev_key)
                .order_by(TitleContentORM.order_key.asc())
                .limit(1)
            ).first()
            return (prev_key, next_row.order_key if next_row else None)
        # default to end
        last = self.db.scalars(
            select(TitleContentORM)
            .where(TitleContentORM.parent_title_id == parent_title_id)
            .order_by(TitleContentORM.order_key.desc())
            .limit(1)
        ).first()
        return (last.order_key if last else None, None)

    # ---- Rebalancing -------------------------------------------------------
    @staticmethod
    def _to_base36(n: int, width: int = 4) -> str:
        if n == 0:
            s = "0"
        else:
            s = ""
            x = n
            while x > 0:
                x, r = divmod(x, 36)
                s = DIGITS[r] + s
        return s.rjust(width, "0")

    def _rebalance(self, parent_title_id: int, width: int = 4) -> None:
        rows = self.db.scalars(
            select(TitleContentORM)
            .where(TitleContentORM.parent_title_id == parent_title_id)
            .order_by(TitleContentORM.order_key.asc())
        ).all()
        n = len(rows)
        if n == 0:
            return
        max_value = 36**width - 1
        step = max(1, max_value // (n + 1))
        for idx, row in enumerate(rows, start=1):
            row.order_key = self._to_base36(idx * step, width)
        self._safe_commit()

    def compute_new_order_key(
        self,
        parent_title_id: int,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        position: str | None = None,
    ) -> str:
        prev_key, next_key = self._get_prev_next_keys(
            parent_title_id,
            before_id=before_id,
            after_id=after_id,
            position=position,
        )

        def _valid_between(k: str) -> bool:
            if prev_key is not None and not (prev_key < k):
                return False
            if next_key is not None and not (k < next_key):
                return False
            # Ensure uniqueness within this parent
            exists = self.db.scalar(
                select(func.count())
                .select_from(TitleContentORM)
                .where(TitleContentORM.parent_title_id == parent_title_id)
                .where(TitleContentORM.order_key == k)
            )
            return (exists or 0) == 0

        # First attempt
        if prev_key is None and next_key is None:
            k = head(None)
        elif prev_key is None:
            k = head(next_key)
        elif next_key is None:
            k = tail(prev_key)
        else:
            k = between(prev_key, next_key)

        if _valid_between(k):
            return k

        # Rebalance and try again once
        self._rebalance(parent_title_id)
        prev_key, next_key = self._get_prev_next_keys(
            parent_title_id,
            before_id=before_id,
            after_id=after_id,
            position=position,
        )
        if prev_key is None and next_key is None:
            return head(None)
        if prev_key is None:
            k2 = head(next_key)
        elif next_key is None:
            k2 = tail(prev_key)
        else:
            k2 = between(prev_key, next_key)
        return k2

    def reorder(
        self,
        parent_title_id: int,
        title_content_id: int,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        position: str | None = None,
    ) -> TitleContentRead | None:
        orm = self.db.get(TitleContentORM, title_content_id)
        if not orm:
            return None
        new_key = self.compute_new_order_key(
            parent_title_id,
            before_id=before_id,
            after_id=after_id,
            position=position,
        )
        orm.order_key = new_key
        orm.parent_title_id = parent_title_id
        self._safe_commit()
        self.db.refresh(orm)
        return TitleContentRead.model_validate(orm)

    def create_positioned(
        self,
        parent_title_id: int,
        title_content: TitleContentInsert,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        position: str | None = None,
    ) -> TitleContentRead | None:
        # Compute a new key and create a proper TitleContentCreate
        new_key = self.compute_new_order_key(
            parent_title_id,
            before_id=before_id,
            after_id=after_id,
            position=position,
        )
        to_create = TitleContentCreateInternal(
            parent_title_id=parent_title_id,
            kind=title_content.kind,
            child_title_id=title_content.child_title_id,
            asset_id=title_content.asset_id,
            label=title_content.label,
            order_key=new_key,
        )
        return self.create(to_create)
