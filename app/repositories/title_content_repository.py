# app/repositories/title_content_repository.py

from collections.abc import Sequence

from sqlalchemy import Integer, delete, func, literal, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.orm import selectinload

from app.models import AssetORM, TitleContentORM, TitleORM
from app.schemas.enums import MembershipKind
from app.schemas import (
    TitleContentCounts,
    TitleContentCreateInternal,
    TitleContentInsert,
    TitleContentRead,
    TitleContentReadExtended,
    TitleContentReadParent,
    TitleContentUpdateInternal,
    TitleMediaTotals,
)
from app.utils.order_key import DIGITS, between, head, tail

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TitleContentRepository

#: How far to follow containment when testing whether an edge would close a cycle.
#:
#: Matches ``MAX_RESOLUTION_DEPTH`` in the artwork repository, because both walk the
#: same graph and a guard that stops shallower than a reader would let through exactly
#: the cycles that reader can still reach.
MAX_CONTAINMENT_DEPTH = 8


class SQLAlchemyTitleContentRepository(SQLAlchemyBaseRepository, TitleContentRepository):
    def can_reach(
        self, start_title_id: int, target_title_id: int, max_depth: int = MAX_CONTAINMENT_DEPTH
    ) -> bool:
        """Whether ``target_title_id`` is reachable from ``start_title_id``.

        Used to answer "would adding parent -> child close a cycle?", which is the same
        question as "can the child already reach the parent?".

        The walk carries the set of titles already on its path and refuses to re-enter
        one, exactly as ``resolve_for_titles`` does. That is not redundant with the
        constraint this supports: the guard has to be safe on a database that *already*
        contains a cycle, which every deployment does until its migration runs -- and a
        depth cap alone would bound the damage rather than avoid the revisit.

        Args:
            start_title_id: The title to walk down from.
            target_title_id: The title being looked for.
            max_depth: How many levels of containment to descend.

        Returns:
            bool: True if the target is reachable, and so the edge would close a cycle.
        """
        seed = (
            select(
                TitleContentORM.child_title_id.label("title_id"),
                literal(1, Integer).label("depth"),
                array([TitleContentORM.parent_title_id, TitleContentORM.child_title_id]).label(
                    "seen"
                ),
            )
            .where(TitleContentORM.parent_title_id == start_title_id)
            .where(TitleContentORM.child_title_id.isnot(None))
        )

        walk = seed.cte("containment_walk", recursive=True)
        step = (
            select(
                TitleContentORM.child_title_id.label("title_id"),
                (walk.c.depth + 1).label("depth"),
                (walk.c.seen + array([TitleContentORM.child_title_id])).label("seen"),
            )
            .select_from(walk)
            .join(TitleContentORM, TitleContentORM.parent_title_id == walk.c.title_id)
            .where(TitleContentORM.child_title_id.isnot(None))
            .where(walk.c.depth < max_depth)
            .where(~walk.c.seen.contains(array([TitleContentORM.child_title_id])))
        )
        walk = walk.union_all(step)

        found = self.db.execute(
            select(literal(1)).select_from(walk).where(walk.c.title_id == target_title_id).limit(1)
        ).first()
        return found is not None

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

    def get_parents_of_title(self, title_id: int) -> list[TitleContentReadParent]:
        """The containment rows naming this title as their child.

        The upward counterpart of :meth:`get_titles_with_asset`, and deliberately the
        same shape: the edge itself plus its parent, so a caller gets the ``label`` and
        ``order_key`` this title carries *within* that parent rather than just the
        parent's identity.

        Immediate parents only. An ancestor walk is a different question, and a
        different answer once a title can have several parents -- see #89.

        Args:
            title_id: The title whose parents to find.

        Returns:
            list[TitleContentReadParent]: Each containment row, with its parent title,
                ordered by parent name.
        """
        stmt = (
            select(TitleContentORM)
            .join(TitleContentORM.parent_title)
            .where(TitleContentORM.child_title_id == title_id)
            .options(selectinload(TitleContentORM.parent_title))
            .order_by(TitleORM.name.asc())
        )
        rows = self.db.scalars(stmt).all()
        return [TitleContentReadParent.model_validate(row) for row in rows]

    def intrinsic_parent_edge_id(self, child_title_id: int) -> int | None:
        """The id of the edge already recording this title's intrinsic parent, if any.

        Backs the service's 409. ``uq_one_intrinsic_parent`` is what actually enforces
        the rule -- this only exists so a caller gets told which edge it collided with
        instead of a bare "unique constraint violated", and so the check also covers the
        patch path, where the row being repointed has to be excluded by id.

        Args:
            child_title_id: The title whose existing intrinsic parent to look for.

        Returns:
            int | None: The containment row's id, or None if the title has no intrinsic
                parent yet.
        """
        return self.db.scalar(
            select(TitleContentORM.id)
            .where(TitleContentORM.child_title_id == child_title_id)
            .where(TitleContentORM.membership == MembershipKind.intrinsic)
            .limit(1)
        )

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
            membership=title_content.membership,
            order_key=new_key,
        )
        return self.create(to_create)

    def counts_for_titles(self, title_ids: Sequence[int]) -> dict[int, TitleContentCounts]:
        """Count the titles and assets each title directly contains.

        **One query for the whole page**, not one per title. A count evaluated per row
        is #49 wearing different clothes -- that shape measured 14.6s at the 500-row
        cap against 263ms without it -- so this groups by parent and the service joins
        the result back onto the page in memory.

        Two deliberate omissions, both of which look like bugs until checked:

        ``DISTINCT`` is absent because it would be redundant.
        ``uq_parent_child_title_once`` and ``uq_parent_asset_once`` are unique on
        (parent, target) with membership *outside* their predicates, so one parent
        cannot hold the same child twice under any membership. Deduplication is
        load-bearing for ``totals_for_titles``, where one asset really can be reached
        by two paths, and is simply not needed here.

        A ``membership`` filter is absent because it would be wrong. Counting intrinsic
        edges only would report every curated collection as containing nothing, and a
        curated list's size is the one number its tile exists to show.

        Args:
            title_ids: The titles to count for. An empty sequence issues no query.

        Returns:
            dict[int, TitleContentCounts]: Title id -> counts, omitting titles that
                contain nothing at all. Callers supply the zero.
        """
        if not title_ids:
            return {}

        stmt = (
            select(
                TitleContentORM.parent_title_id,
                func.count()
                .filter(TitleContentORM.child_title_id.isnot(None))
                .label("child_count"),
                func.count().filter(TitleContentORM.asset_id.isnot(None)).label("asset_count"),
            )
            .where(TitleContentORM.parent_title_id.in_(title_ids))
            .group_by(TitleContentORM.parent_title_id)
        )
        return {
            parent_id: TitleContentCounts(child_count=children, asset_count=assets)
            for parent_id, children, assets in self.db.execute(stmt).all()
        }

    def totals_for_titles(
        self, title_ids: Sequence[int], max_depth: int = MAX_CONTAINMENT_DEPTH
    ) -> dict[int, TitleMediaTotals]:
        """Sum the runtime and size of every distinct asset beneath each title.

        **One query for the whole page**, for the same reason as ``counts_for_titles``.

        Only intrinsic edges are followed. ``uq_one_intrinsic_parent`` allows a child
        at most one intrinsic parent, so the intrinsic graph is a forest and no title
        is reached twice from one root. Following curated edges as well would sum a
        borrowed title's runtime into every list that borrowed it.

        **The deduplication that matters here is over assets, not titles.** Only
        ``uq_parent_asset_once`` constrains assets, and it is scoped to a single
        parent -- the same asset under two different titles in one subtree is
        explicitly ordinary (the same file under two cuts, per ``TitleContentORM``).
        Summing the join directly would count that file's runtime twice, so the asset
        set is made distinct per root before it is summed. A fixture with one asset
        per title cannot tell the two apart.

        The walk carries the set of titles already on its path and refuses to re-enter
        one, exactly as ``can_reach`` and ``resolve_for_titles`` do. That is not made
        redundant by ``uq_one_intrinsic_parent``: at most one intrinsic parent each
        still permits a cycle among titles whose parents point round a loop, and the
        guard has to be safe on a database that already contains one.

        Args:
            title_ids: The titles to total for. An empty sequence issues no query.
            max_depth: How many levels of intrinsic containment to descend.

        Returns:
            dict[int, TitleMediaTotals]: Title id -> totals, omitting titles with no
                assets beneath them. Callers supply the zero.
        """
        if not title_ids:
            return {}

        # Depth 0: each requested title standing for itself, so assets hanging
        # directly off it are included alongside those further down.
        seed = select(
            TitleORM.id.label("root_id"),
            TitleORM.id.label("title_id"),
            literal(0, Integer).label("depth"),
            array([TitleORM.id]).label("seen"),
        ).where(TitleORM.id.in_(title_ids))

        walk = seed.cte("totals_walk", recursive=True)
        step = (
            select(
                walk.c.root_id,
                TitleContentORM.child_title_id.label("title_id"),
                (walk.c.depth + 1).label("depth"),
                (walk.c.seen + array([TitleContentORM.child_title_id])).label("seen"),
            )
            .select_from(walk)
            .join(TitleContentORM, TitleContentORM.parent_title_id == walk.c.title_id)
            .where(TitleContentORM.child_title_id.isnot(None))
            .where(TitleContentORM.membership == MembershipKind.intrinsic)
            .where(walk.c.depth < max_depth)
            .where(~walk.c.seen.contains(array([TitleContentORM.child_title_id])))
        )
        walk = walk.union_all(step)

        # DISTINCT before SUM: one asset reachable under two titles in the same
        # subtree must contribute its runtime once, not once per path.
        reachable = (
            select(
                walk.c.root_id,
                AssetORM.id.label("asset_id"),
                AssetORM.duration,
                AssetORM.size,
            )
            .select_from(walk)
            .join(TitleContentORM, TitleContentORM.parent_title_id == walk.c.title_id)
            .join(AssetORM, AssetORM.id == TitleContentORM.asset_id)
            .distinct()
            .subquery("reachable_assets")
        )

        stmt = select(
            reachable.c.root_id,
            func.coalesce(func.sum(reachable.c.duration), 0),
            func.coalesce(func.sum(reachable.c.size), 0),
        ).group_by(reachable.c.root_id)

        return {
            root_id: TitleMediaTotals(total_runtime=float(runtime), total_size=int(size))
            for root_id, runtime, size in self.db.execute(stmt).all()
        }
