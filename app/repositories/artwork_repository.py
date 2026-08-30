# app/repositories/artwork_repository.py
from collections.abc import Sequence

from sqlakeyset import select_page
from sqlalchemy import Integer, func, literal, select
from sqlalchemy.sql import ColumnElement, Select
from sqlalchemy.dialects.postgresql import array

from app.models import ArtworkKindORM, ArtworkORM, TitleContentORM, TitleORM
from app.models.sort_configs import ARTWORK_SORT
from app.schemas import (
    ArtworkCreateInternal,
    ArtworkKindCreateInternal,
    ArtworkKindRead,
    ArtworkKindUpdateInternal,
    ArtworkListParams,
    ArtworkRead,
    ArtworkUpdateInternal,
    PaginatedResponse,
)
from app.schemas.enums import EntityTypeEnum, MembershipKind

from ..utils.sorting import apply_ordering

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import ArtworkKindRepository, ArtworkRepository

#: How far down a title's contents to look for artwork it can borrow.
#:
#: Deep enough for the nesting the data actually has -- collection, series, season,
#: episode is four -- with headroom, and shallow enough that a pathological chain
#: cannot make a page read expensive. The measured maximum contents list is 35 wide;
#: depth is the dimension that multiplies.
MAX_RESOLUTION_DEPTH = 8

#: The containment edges a display image may be borrowed along.
#:
#: Defined once and used by both directions of the walk -- ``resolve_for_titles``
#: descending to find a title's image, and ``titles_resolving_artwork`` ascending to
#: find which titles have one. If those two disagreed about which edges count, the
#: ``resolves_display_image`` filter would return titles whose image is null and omit
#: titles that show one, which is a worse failure than not having the filter (#122).
BORROWABLE = TitleContentORM.membership == MembershipKind.intrinsic


def titles_resolving_artwork(
    kind_ids: Sequence[int], max_depth: int = MAX_RESOLUTION_DEPTH
) -> Select:
    """Ids of every title that resolves artwork of any of ``kind_ids``.

    The set ``resolve_for_titles`` would return something for, computed as a set rather
    than per title, so it can be semi-joined against a listing (#122).

    **Walks up, not down**, which is what makes it affordable as a filter. Descending
    from the listing's candidates means walking the containment graph once per
    candidate -- the shape #49 measured at 14.6s -- because the work scales with the
    number of titles asked about. Ascending scales with the number of *artworks*
    instead: the seed is the artwork table, and ``uq_one_intrinsic_parent`` allows a
    title at most one intrinsic parent, so each step upward is a chain rather than a
    fan-out. Assets are the one place it branches, since the same file may sit under
    two cuts.

    Measured at 102,500 containment rows against a 500-row page: 150ms ascending,
    311ms descending, and 74ms for the ``include=display_image`` resolution the same
    grid already pays for on the same page. At today's production shape it is 3.7ms.
    So the filter is the same order of magnitude as the call it accompanies, which is
    the bar #122 set. A materialised "resolved image" column maintained on write --
    the alternative the issue names -- buys roughly one order of magnitude more and
    costs a write path and a backfill; it is not needed at this size.

    Both directions must agree, or the filter lies about the listing it filters. They
    share ``BORROWABLE``, and ``TestFilterAgreesWithResolution`` asserts the agreement
    on built data rather than trusting that they were written to match.

    Args:
        kind_ids: The artwork kinds that count as a display image, already resolved
            from codes by the service, as everywhere else in this repository.
        max_depth: How many levels of containment to ascend. The same cap the
            descending walk applies, so the two see the same reachable set.

    Returns:
        Select: A one-column selectable of title ids, for use as a semi-join target.
            Selects nothing when ``kind_ids`` is empty.
    """
    if not kind_ids:
        return select(TitleORM.id).where(literal(False))

    holders = (
        select(ArtworkORM.entity_type, ArtworkORM.entity_id)
        .where(ArtworkORM.artwork_kind_id.in_(kind_ids))
        .where(ArtworkORM.is_primary.is_(True))
        .distinct()
        .cte("artwork_holders")
    )

    # Depth 0: a title holding its own artwork resolves for itself, which is the
    # "own artwork wins" case arriving at the same answer from the other side.
    own = select(
        holders.c.entity_id.label("title_id"),
        literal(0, Integer).label("depth"),
        array([holders.c.entity_id]).label("seen"),
    ).where(holders.c.entity_type == EntityTypeEnum.title)

    # An asset is a leaf: its holders are the titles that directly contain it.
    from_asset = (
        select(
            TitleContentORM.parent_title_id.label("title_id"),
            literal(1, Integer).label("depth"),
            array([TitleContentORM.parent_title_id]).label("seen"),
        )
        .select_from(holders)
        .join(TitleContentORM, TitleContentORM.asset_id == holders.c.entity_id)
        .where(holders.c.entity_type == EntityTypeEnum.asset)
        .where(BORROWABLE)
    )

    # Wrapped in a subquery because a recursive CTE's seed has to be a single SELECT:
    # SQLAlchemy refuses `union_all` onto a CTE whose element is already a compound.
    seeds = own.union_all(from_asset).subquery("artwork_seed")
    seed = select(seeds.c.title_id, seeds.c.depth, seeds.c.seen)

    climb = seed.cte("artwork_climb", recursive=True)
    # The same two guards the descending walk carries, for the same reason: the cap
    # bounds legitimate depth and the path set bounds a cycle, which #88 leaves open.
    step = (
        select(
            TitleContentORM.parent_title_id.label("title_id"),
            (climb.c.depth + 1).label("depth"),
            (climb.c.seen + array([TitleContentORM.parent_title_id])).label("seen"),
        )
        .select_from(climb)
        .join(TitleContentORM, TitleContentORM.child_title_id == climb.c.title_id)
        .where(BORROWABLE)
        .where(climb.c.depth < max_depth)
        .where(~climb.c.seen.contains(array([TitleContentORM.parent_title_id])))
    )
    climb = climb.union_all(step)

    return select(climb.c.title_id).distinct()


class SQLAlchemyArtworkKindRepository(SQLAlchemyBaseRepository, ArtworkKindRepository):
    """The artwork kinds an artwork can be categorised as.

    Mirrors ``SQLAlchemyTitleTypeRepository`` deliberately -- this is the same lookup
    table shape, for the same reason (#41).
    """

    def create(self, kind: ArtworkKindCreateInternal) -> ArtworkKindRead:
        orm = ArtworkKindORM(**kind.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return ArtworkKindRead.model_validate(orm, from_attributes=True)

    def get(self, kind_id: int) -> ArtworkKindRead | None:
        orm = self.db.get(ArtworkKindORM, kind_id)
        return ArtworkKindRead.model_validate(orm, from_attributes=True) if orm else None

    def exists(self, kind_id: int) -> bool:
        return self.db.get(ArtworkKindORM, kind_id) is not None

    def get_by_code(self, code: str) -> ArtworkKindRead | None:
        stmt = select(ArtworkKindORM).where(ArtworkKindORM.code == code)
        orm = self.db.scalars(stmt).first()
        return ArtworkKindRead.model_validate(orm, from_attributes=True) if orm else None

    def list_all(self) -> list[ArtworkKindRead]:
        rows = self.db.scalars(select(ArtworkKindORM).order_by(ArtworkKindORM.code)).all()
        return [ArtworkKindRead.model_validate(row, from_attributes=True) for row in rows]

    def update(
        self,
        kind_id: int,
        update: ArtworkKindUpdateInternal,  # type: ignore
    ) -> ArtworkKindRead:
        orm = self.db.get(ArtworkKindORM, kind_id)
        if not orm:
            raise NotFoundError

        update_data = update.model_dump(exclude_unset=True)  # type: ignore
        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return ArtworkKindRead.model_validate(orm, from_attributes=True)

    def delete(self, kind_id: int) -> None:
        orm = self.db.get(ArtworkKindORM, kind_id)
        if not orm:
            raise NotFoundError
        self.db.delete(orm)
        self._safe_commit()

    def usage_count(self, kind_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(ArtworkORM)
            .where(ArtworkORM.artwork_kind_id == kind_id)
        )
        return self.db.scalar(stmt) or 0


class SQLAlchemyArtworkRepository(SQLAlchemyBaseRepository, ArtworkRepository):
    """Artwork rows, scoped to the title or asset they belong to.

    Every read here is scoped to one entity, so none of them paginate: the number of
    artworks an entity can hold is bounded by the number of kinds, and a primary
    lookup returns at most one row. If that ever stops being true -- a gallery of
    dozens of stills per episode, say -- this is where a page cap has to arrive,
    because #51 is what an uncapped collection read costs once the data grows.
    """

    def create(self, artwork: ArtworkCreateInternal) -> ArtworkRead:
        orm = ArtworkORM(**artwork.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return ArtworkRead.model_validate(orm, from_attributes=True)

    def get(self, artwork_id: int) -> ArtworkRead | None:
        orm = self.db.get(ArtworkORM, artwork_id)
        return ArtworkRead.model_validate(orm, from_attributes=True) if orm else None

    def list_paged(
        self, params: ArtworkListParams, kind_id: int | None = None
    ) -> PaginatedResponse[ArtworkRead]:
        """A page of artwork across every entity.

        The kind arrives already resolved to an id: the code -> id lookup belongs to
        the service, which raises the same 422 for an unknown code here as it does on
        the nested routes. A repository that silently returned an empty page instead
        would make a typo indistinguishable from a kind nothing uses.

        Args:
            params: Filters, sort and cursor.
            kind_id: The resolved artwork kind to restrict to, if `params.kind` was set.

        Returns:
            PaginatedResponse[ArtworkRead]: The page and its cursors.
        """
        stmt = select(ArtworkORM)

        if params.entity_type is not None:
            stmt = stmt.where(ArtworkORM.entity_type == params.entity_type)
        if params.entity_id is not None:
            stmt = stmt.where(ArtworkORM.entity_id == params.entity_id)
        if kind_id is not None:
            stmt = stmt.where(ArtworkORM.artwork_kind_id == kind_id)
        if params.is_primary is not None:
            stmt = stmt.where(ArtworkORM.is_primary.is_(params.is_primary))

        stmt = apply_ordering(stmt, ARTWORK_SORT, params.sort)

        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        rows = [row[0] for row in list(page)]

        return PaginatedResponse[ArtworkRead](
            items=[ArtworkRead.model_validate(row) for row in rows],
            page=self._page_info(page),
        )

    def list_for_entity(
        self, entity_type: EntityTypeEnum, entity_id: int, kind_id: int | None = None
    ) -> list[ArtworkRead]:
        stmt = select(ArtworkORM).where(
            ArtworkORM.entity_type == entity_type,
            ArtworkORM.entity_id == entity_id,
        )
        if kind_id is not None:
            stmt = stmt.where(ArtworkORM.artwork_kind_id == kind_id)
        # Primary first, then oldest first, so a caller taking the head of the list
        # gets the primary without having to ask for it.
        stmt = stmt.order_by(ArtworkORM.is_primary.desc(), ArtworkORM.id)
        rows = self.db.scalars(stmt).all()
        return [ArtworkRead.model_validate(row, from_attributes=True) for row in rows]

    def get_primary(
        self, entity_type: EntityTypeEnum, entity_id: int, kind_id: int
    ) -> ArtworkRead | None:
        """The one artwork marked primary for an entity and kind.

        Served by ``ix_artwork_entity_kind_primary``, and guaranteed to match at most
        one row by ``uq_artwork_one_primary_per_kind``.
        """
        stmt = select(ArtworkORM).where(
            ArtworkORM.entity_type == entity_type,
            ArtworkORM.entity_id == entity_id,
            ArtworkORM.artwork_kind_id == kind_id,
            ArtworkORM.is_primary.is_(True),
        )
        orm = self.db.scalars(stmt).first()
        return ArtworkRead.model_validate(orm, from_attributes=True) if orm else None

    def update(
        self,
        artwork_id: int,
        update: ArtworkUpdateInternal,  # type: ignore
    ) -> ArtworkRead:
        orm = self.db.get(ArtworkORM, artwork_id)
        if not orm:
            raise NotFoundError

        update_data = update.model_dump(exclude_unset=True)  # type: ignore
        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return ArtworkRead.model_validate(orm, from_attributes=True)

    def set_primary(self, artwork_id: int) -> ArtworkRead:
        """Make one artwork the primary for its entity and kind.

        Demotes the incumbent and promotes the target in a single transaction. Both
        statements have to land together: committing the promotion first would violate
        ``uq_artwork_one_primary_per_kind`` while two rows claim the flag, and
        committing the demotion first would leave the entity with no primary at all if
        the promotion then failed.

        Args:
            artwork_id: The artwork to promote.

        Returns:
            ArtworkRead: The promoted artwork.

        Raises:
            NotFoundError: If no artwork has that ID.
        """
        orm = self.db.get(ArtworkORM, artwork_id)
        if not orm:
            raise NotFoundError

        incumbents = self.db.scalars(
            select(ArtworkORM).where(
                ArtworkORM.entity_type == orm.entity_type,
                ArtworkORM.entity_id == orm.entity_id,
                ArtworkORM.artwork_kind_id == orm.artwork_kind_id,
                ArtworkORM.is_primary.is_(True),
                ArtworkORM.id != artwork_id,
            )
        ).all()
        for incumbent in incumbents:
            incumbent.is_primary = False
        # Flush the demotions before the promotion so the unique index never sees two
        # rows claiming the flag within the statement order of this transaction.
        self.db.flush()

        orm.is_primary = True
        self._safe_commit()
        self.db.refresh(orm)
        return ArtworkRead.model_validate(orm, from_attributes=True)

    def delete(self, artwork_id: int) -> None:
        orm = self.db.get(ArtworkORM, artwork_id)
        if not orm:
            raise NotFoundError
        self.db.delete(orm)
        self._safe_commit()

    def resolve_for_titles(
        self, title_ids: Sequence[int], kind_id: int, max_depth: int = MAX_RESOLUTION_DEPTH
    ) -> dict[int, ArtworkRead]:
        """Resolve each title's artwork of a kind, falling back to its contents.

        A title uses its own primary artwork if it has one. Otherwise it borrows from
        the first entry of its contents, in ``position`` order, recursing into child
        titles -- so a season with no poster shows its first episode's, and an episode
        with none shows its asset's. A title with nothing beneath it resolves to
        nothing, which is the browse grid's placeholder case rather than an error.

        **One query for the whole page**, not one per title. ``GET /api/titles/`` caps
        at 500 rows, and a resolution walk evaluated per row is #49 again -- a
        relationship serialised into a list response at one extra SELECT per row, which
        measured 14.6s at the cap against 263ms without it.

        The walk is over a **DAG, not a tree**: containment nests, and whether cycles
        are prevented at all is still open (#88). The depth cap alone would not save a
        cycle -- it would bound the damage, not avoid revisiting -- so the recursive
        term also carries the set of titles already on its path and refuses to re-enter
        one. Either guard alone is insufficient; the cap bounds legitimate depth, the
        path set bounds illegitimate repetition.

        **Only intrinsic edges are walked.** A curated edge says nothing about where
        its child belongs -- that is the whole point of the distinction #90 drew -- so
        borrowing an image across one would give "Films of 1974" the identity of
        whichever unrelated member happens to sort first by ``position``, and the grid
        would present that as the collection's own. A title reached only through
        curated edges therefore resolves to nothing and gets the placeholder, which is
        the same answer this already gives a title with no contents, and the correct
        one for a list nobody has given an image to (#161).

        This restricts *borrowing*, not a title's own artwork: the seed is unfiltered,
        so a curated collection that has been given its own primary artwork still
        resolves it. Being able to say so is what makes the restriction safe rather
        than merely stricter.

        The predicate needs no index of its own. It filters rows the join has already
        fetched, and the parent-side indexes (``uq_parent_position``,
        ``uq_parent_child_title_once``) select one parent's contents, which is 35 rows
        at the measured widest. ``ix_title_contents_child_membership`` does not serve
        this walk at all, being keyed on ``child_title_id`` while this joins on
        ``parent_title_id`` -- worth stating because its name suggests otherwise.

        Measured at 102,500 containment rows (17,500 of them curated), resolving a
        full 500-row page: 75.3ms against a 75.2ms baseline that walked every edge, so
        the restriction is free. A page of nothing but curated lists drops from 59.0ms
        to 19.2ms, because the walk now stops at the first edge instead of descending
        the whole list. That page also went from resolving 500 of 500 to 0 of 500,
        which is the bug rather than a regression: every one of those images was
        arbitrary.

        Args:
            title_ids: The titles to resolve for. An empty sequence issues no query.
            kind_id: The artwork kind to resolve, e.g. the id of ``poster``.
            max_depth: How many levels of containment to descend.

        Returns:
            dict[int, ArtworkRead]: Title id -> resolved artwork, omitting titles that
                resolved to nothing.
        """
        if not title_ids:
            return {}

        # Depth 0: each requested title, standing for itself, so its own artwork wins
        # before anything beneath it is considered.
        #
        # `ord` accumulates the position of each edge taken, which is what makes
        # "the first entry of its contents" mean the same thing at every level.
        #
        # An integer array, so the comparison at the bottom is numeric. The text keys
        # this replaced (#128) needed an explicit collation="C" element type to match
        # the column's, because a recursive CTE whose seed and recursive terms disagree
        # on collation is rejected outright by Postgres -- a hazard that simply does not
        # arise for integers.
        seed = select(
            TitleORM.id.label("root_id"),
            TitleORM.id.label("title_id"),
            literal(None, Integer).label("asset_id"),
            literal(0, Integer).label("depth"),
            array([], type_=Integer).label("ord"),
            array([TitleORM.id]).label("seen"),
        ).where(TitleORM.id.in_(title_ids))

        walk = seed.cte("artwork_walk", recursive=True)
        step = (
            select(
                walk.c.root_id,
                TitleContentORM.child_title_id.label("title_id"),
                TitleContentORM.asset_id.label("asset_id"),
                (walk.c.depth + 1).label("depth"),
                (walk.c.ord + array([TitleContentORM.position])).label("ord"),
                (walk.c.seen + array([TitleContentORM.child_title_id])).label("seen"),
            )
            .select_from(walk)
            .join(TitleContentORM, TitleContentORM.parent_title_id == walk.c.title_id)
            # Only titles have contents; an asset row is a leaf.
            .where(walk.c.title_id.isnot(None))
            .where(walk.c.depth < max_depth)
            # Borrow down a child's home, never across a curated list (#161).
            .where(BORROWABLE)
            .where(~walk.c.seen.contains(array([TitleContentORM.child_title_id])))
        )
        walk = walk.union_all(step)

        # Two index-friendly joins unioned, rather than one join with an OR across
        # entity_type: an OR cannot use ix_artwork_entity_kind_primary, and this read
        # exists to be cheap.
        def _matches(entity_type: EntityTypeEnum, column: ColumnElement[int | None]) -> Select:
            return (
                select(
                    walk.c.root_id,
                    ArtworkORM.id.label("artwork_id"),
                    walk.c.depth,
                    walk.c.ord,
                )
                .select_from(walk)
                .join(
                    ArtworkORM,
                    (ArtworkORM.entity_type == entity_type) & (ArtworkORM.entity_id == column),
                )
                .where(ArtworkORM.artwork_kind_id == kind_id)
                .where(ArtworkORM.is_primary.is_(True))
            )

        matches = (
            _matches(EntityTypeEnum.title, walk.c.title_id)
            .union_all(_matches(EntityTypeEnum.asset, walk.c.asset_id))
            .subquery("artwork_matches")
        )
        nearest = (
            select(matches.c.root_id, matches.c.artwork_id)
            .distinct(matches.c.root_id)
            .order_by(matches.c.root_id, matches.c.depth, matches.c.ord)
            .subquery("artwork_nearest")
        )

        rows = self.db.execute(
            select(nearest.c.root_id, ArtworkORM).join(
                ArtworkORM, ArtworkORM.id == nearest.c.artwork_id
            )
        ).all()
        return {
            root_id: ArtworkRead.model_validate(orm, from_attributes=True) for root_id, orm in rows
        }

    def count_for_entity(self, entity_type: EntityTypeEnum, entity_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(ArtworkORM)
            .where(
                ArtworkORM.entity_type == entity_type,
                ArtworkORM.entity_id == entity_id,
            )
        )
        return self.db.scalar(stmt) or 0
