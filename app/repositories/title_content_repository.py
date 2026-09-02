# app/repositories/title_content_repository.py

from collections.abc import Sequence

from sqlalchemy import Integer, func, literal, select
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
from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TitleContentRepository

#: How far to follow containment when testing whether an edge would close a cycle.
#:
#: Matches ``MAX_RESOLUTION_DEPTH`` in the artwork repository, because both walk the
#: same graph and a guard that stops shallower than a reader would let through exactly
#: the cycles that reader can still reach.
MAX_CONTAINMENT_DEPTH = 8


def target_index(
    ordered_ids: Sequence[int],
    *,
    before_id: int | None = None,
    after_id: int | None = None,
    anchor: str | None = None,
) -> int | None:
    """Where in ``ordered_ids`` a row should land, given a positioning request.

    Pure, and deliberately so: this is the whole of the positioning logic, and it is
    worth being able to test it without a database. ``ordered_ids`` is the list *as it
    will be without the row being placed*, so a move is "remove, then insert here" and
    the returned index needs no correction for the mover's own former slot.

    Args:
        ordered_ids: The parent's rows in position order, excluding the row being
            placed.
        before_id: Land immediately before this row.
        after_id: Land immediately after this row.
        anchor: ``"start"`` or ``"end"``. Any other value is ignored, which is how the
            router's free-form query parameter has always behaved.

    Returns:
        int | None: The index to insert at, or None if ``before_id``/``after_id`` named
            a row that is not in this list -- a row of some other parent, or the row
            being placed named as its own neighbour. Callers decide what that means,
            because the sensible answer differs between an insert and a move.
    """
    if anchor == "start":
        return 0
    if anchor == "end":
        return len(ordered_ids)
    if before_id is not None:
        return ordered_ids.index(before_id) if before_id in ordered_ids else None
    if after_id is not None:
        return ordered_ids.index(after_id) + 1 if after_id in ordered_ids else None
    return len(ordered_ids)


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
            .order_by(TitleContentORM.position)
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

        source_parent_id = orm.parent_title_id

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        # A patch that repoints the row at a different parent moves it between two
        # ordered lists, and both have to stay contiguous afterwards. The row keeps no
        # meaningful place in a list it has just joined, so it appends.
        #
        # No API path reaches this any more. `update_title_content` used to set
        # `parent_title_id` on *every* patch, from the URL, which meant a request that
        # changed only a label silently relocated the row -- #185 -- and it no longer
        # forwards the field at all. Kept because this method takes a partial
        # `TitleContentUpdateInternal` in which `parent_title_id` is settable, so a
        # caller that does set it must not be able to leave two lists with holes in
        # them. Under the previous scheme the row simply carried its old key across,
        # which was harmless only because nothing required those keys to mean anything.
        if orm.parent_title_id != source_parent_id:
            self._safe_flush()
            lists = self._locked_lists({source_parent_id, orm.parent_title_id})
            self._renumber([row for row in lists[source_parent_id] if row.id != orm.id])
            self._renumber([row for row in lists[orm.parent_title_id] if row.id != orm.id] + [orm])

        self._safe_commit()
        self.db.refresh(orm)
        return TitleContentRead.model_validate(orm, from_attributes=True)

    def delete_title_content(self, title_content_id: int) -> None:
        """Remove an entry and close the gap it leaves in its parent's list.

        Renumbering on delete is what a scheme of contiguous positions costs that a
        sparse key does not. Skipping it would not misorder anything -- the survivors
        still sort correctly around the hole -- but it would make ``position`` a number
        that only sometimes means "the nth entry", and being able to read it as exactly
        that is the point of #128.

        Args:
            title_content_id: The entry to remove. Unknown ids are a no-op, as before.
        """
        # Which parent, without loading the row -- the same column-only read `reorder`
        # uses, and for the same reason: the entity is taken from the locked lists.
        parent_title_id = self.db.scalar(
            select(TitleContentORM.parent_title_id).where(TitleContentORM.id == title_content_id)
        )
        if parent_title_id is None:
            return

        # Lock the parent **before** touching the row (#193). Deleting first took a lock
        # on the contents row and the parent second, which is the opposite order to
        # every other write here -- so a delete and a reorder on one parent could each
        # hold what the other needed. Nothing in the API paired them, which is why it
        # never showed up; normalising the order is cheaper than relying on that.
        lists = self._locked_lists({parent_title_id})

        orm = next((row for row in lists[parent_title_id] if row.id == title_content_id), None)
        if orm is None:
            # Deleted by a concurrent caller between the read above and the lock. Their
            # transaction renumbered the list; there is nothing left to do.
            return

        self.db.delete(orm)
        # Flushed explicitly rather than left to autoflush, which the session factory
        # disables: the renumber below reads the list back, and it has to read it
        # without the row that is on its way out.
        self._safe_flush()
        self._renumber([row for row in lists[parent_title_id] if row.id != title_content_id])
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
        ``position`` this title carries *within* that parent rather than just the
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

    # ---- Ordering ----------------------------------------------------------
    def _locked_lists(self, parent_title_ids: set[int]) -> dict[int, list[TitleContentORM]]:
        """Each named parent's rows in position order, locked for the transaction.

        Every write below is read-modify-write over a whole list: two concurrent moves
        under one parent would otherwise both renumber from the same starting picture
        and the second would undo the first. The previous key-based scheme took no lock
        at all and raced the same way, less visibly.

        The lock that makes that safe is on the **parent titles**, not on the contents
        rows -- see the comment in the body for why locking the contents alone did not
        work, and what it cost. Both parents of a cross-parent move are locked by one
        statement in id order, so opposing moves take them the same way round and
        cannot deadlock.

        Args:
            parent_title_ids: The parents whose lists are about to be rewritten.

        Returns:
            dict[int, list[TitleContentORM]]: Parent id -> its rows, ascending by
                position. A parent with no contents maps to an empty list.
        """
        # Lock the **parent title rows** first, in id order, in one statement.
        #
        # This is the serialisation point, and locking the contents rows is not
        # (#193). `FOR UPDATE` locks the rows it returned, and the row a concurrent
        # append is about to insert is a phantom -- outside that set -- so two appends
        # computed the same `len(rows)` and one lost at commit to the deferred
        # `uq_parent_position`. Measured: 32 concurrent attaches to one parent landed
        # 12 and rejected 20, and a parent already holding rows raced identically,
        # because how many rows exist to lock is irrelevant when the contended value is
        # a row that does not exist yet.
        #
        # A title row exists whether or not its list does, so it can be locked before
        # the first child and still serialise the append. Taking it *before* any
        # contents row is also what stops opposing cross-parent moves deadlocking:
        # `reorder` used to lock its own edge first and then the lists, so two moves in
        # opposite directions each held the row the other needed. That deadlock was
        # real -- Postgres detected it -- despite the comment here that once claimed it
        # could not happen.
        #
        # Ordered by id so that a two-parent move always takes them the same way round
        # whichever direction the edge is travelling.
        self.db.execute(
            select(TitleORM.id)
            .where(TitleORM.id.in_(sorted(parent_title_ids)))
            .order_by(TitleORM.id)
            .with_for_update()
        ).all()

        lists: dict[int, list[TitleContentORM]] = {pid: [] for pid in parent_title_ids}
        rows = self.db.scalars(
            select(TitleContentORM)
            .where(TitleContentORM.parent_title_id.in_(sorted(parent_title_ids)))
            .order_by(TitleContentORM.parent_title_id, TitleContentORM.position)
            .with_for_update()
        ).all()
        for row in rows:
            lists[row.parent_title_id].append(row)
        return lists

    @staticmethod
    def _renumber(rows: Sequence[TitleContentORM]) -> None:
        """Assign contiguous positions 0..n-1 in the order given.

        Rows already holding their target position are left untouched, so an
        append -- the overwhelmingly common write, and the only one production has
        ever performed -- dirties exactly one row rather than the whole list.

        Args:
            rows: The parent's rows in their intended final order.
        """
        for index, row in enumerate(rows):
            if row.position != index:
                row.position = index

    def reorder(
        self,
        parent_title_id: int,
        title_content_id: int,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        anchor: str | None = None,
    ) -> TitleContentRead | None:
        """Move a row to a new place in ``parent_title_id``'s list.

        The row is taken out of the list, an index is chosen against what remains, and
        the list is renumbered around it. Expressing a move that way is what makes the
        awkward cases fall out for free: dropping an item back where it already was, or
        onto either end, is arithmetic on a list rather than a search for a gap that may
        not exist.

        ``parent_title_id`` is applied to the row, so this also moves an entry between
        parents. When it does, the parent being left is renumbered too -- contiguous
        positions have to stay contiguous on both sides, which a scheme built on gaps
        never had to care about.

        Args:
            parent_title_id: The parent the row should belong to afterwards.
            title_content_id: The row to move.
            before_id: Place it immediately before this row.
            after_id: Place it immediately after this row.
            anchor: ``"start"`` or ``"end"``.

        Returns:
            TitleContentRead | None: The moved row, or None if no such row exists.
        """
        # Learn which parent the row is under, without locking it and without loading
        # it. Locking it here is what deadlocked opposing cross-parent moves (#193):
        # each move held its own edge and then waited on the other's list, which
        # contains the other's edge. Parent titles are the first thing locked now, and
        # every contents row is taken after them, by `_locked_lists`.
        #
        # A column-only select, not `db.get`, deliberately. Loading the entity here
        # would put it in the identity map with its pre-lock `parent_title_id`, and the
        # fresh read inside `_locked_lists` would hand that same instance back rather
        # than overwriting the attribute -- so the staleness check below would compare
        # the row against itself and never fire.
        source_parent_id = self.db.scalar(
            select(TitleContentORM.parent_title_id).where(TitleContentORM.id == title_content_id)
        )
        if source_parent_id is None:
            return None

        lists = self._locked_lists({source_parent_id, parent_title_id})

        # Take the row from the locked lists, never from the read above. The window
        # between that read and the lock is small but real: a concurrent move could have
        # taken the row to a third parent, whose list is not one of the two now held.
        # Returning None reports it as "no such row here", which is what it is from this
        # caller's position -- the alternative is renumbering a list we never locked.
        orm = next(
            (row for row in lists[source_parent_id] if row.id == title_content_id),
            None,
        )
        if orm is None:
            return None

        remaining = [row for row in lists[parent_title_id] if row.id != title_content_id]
        index = target_index(
            [row.id for row in remaining],
            before_id=before_id,
            after_id=after_id,
            anchor=anchor,
        )
        if index is None:
            # The neighbour named is not in this list -- another parent's row, or this
            # row offered as its own neighbour. Leave the entry where it is rather than
            # flinging it to the end on what is most likely a caller's mistake.
            index = min(orm.position, len(remaining))

        orm.parent_title_id = parent_title_id
        self._renumber(remaining[:index] + [orm] + remaining[index:])
        if source_parent_id != parent_title_id:
            self._renumber([row for row in lists[source_parent_id] if row.id != title_content_id])

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
        anchor: str | None = None,
    ) -> TitleContentRead | None:
        """Add a row to ``parent_title_id``'s list at the requested place.

        Args:
            parent_title_id: The parent to add the row under.
            title_content: The entry to create.
            before_id: Place it immediately before this row.
            after_id: Place it immediately after this row.
            anchor: ``"start"`` or ``"end"``. Absent, the row is appended, which is what
                ``POST /api/titles/{id}/contents`` asks for.

        Returns:
            TitleContentRead | None: The created row.
        """
        rows = self._locked_lists({parent_title_id})[parent_title_id]
        index = target_index(
            [row.id for row in rows],
            before_id=before_id,
            after_id=after_id,
            anchor=anchor,
        )
        if index is None:
            # An unknown neighbour cannot mean "leave it where it is" for a row that has
            # no place yet, so a new entry appends.
            index = len(rows)

        orm = TitleContentORM(
            parent_title_id=parent_title_id,
            kind=title_content.kind,
            child_title_id=title_content.child_title_id,
            asset_id=title_content.asset_id,
            label=title_content.label,
            membership=title_content.membership,
            position=index,
        )
        self.db.add(orm)
        self._renumber(rows[:index] + [orm] + rows[index:])
        self._safe_commit()
        self.db.refresh(orm)
        return TitleContentRead.model_validate(orm)

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
