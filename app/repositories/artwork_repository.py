# app/repositories/artwork_repository.py
from collections.abc import Sequence

from sqlakeyset import select_page
from sqlalchemy import Integer, Text, func, literal, or_, select
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
from app.schemas.enums import EntityTypeEnum

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
        if params.missing_dimensions is not None:
            missing = or_(ArtworkORM.width.is_(None), ArtworkORM.height.is_(None))
            # `~missing` rather than "both are not null" spelled out again, so the two
            # branches cannot drift into disagreeing about what "missing" means.
            stmt = stmt.where(missing if params.missing_dimensions else ~missing)

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
        the first entry of its contents, in ``order_key`` order, recursing into child
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

        Every containment edge is walked. Once #90 distinguishes intrinsic from curated
        containment, only intrinsic edges should be followed -- a curated collection
        borrowing its poster from an unrelated member is probably wrong -- but that
        distinction does not exist yet, and inventing it here would guess at it.

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
        # `ord` accumulates the order_key of each edge taken, which is what makes
        # "the first entry of its contents" mean the same thing at every level. Its
        # element type carries collation="C" to match `title_contents.order_key`:
        # that column is deliberately C-collated so LexoRank keys order bytewise, and
        # a recursive CTE whose seed and recursive terms disagree on collation is
        # rejected outright by Postgres.
        seed = select(
            TitleORM.id.label("root_id"),
            TitleORM.id.label("title_id"),
            literal(None, Integer).label("asset_id"),
            literal(0, Integer).label("depth"),
            array([], type_=Text(collation="C")).label("ord"),
            array([TitleORM.id]).label("seen"),
        ).where(TitleORM.id.in_(title_ids))

        walk = seed.cte("artwork_walk", recursive=True)
        step = (
            select(
                walk.c.root_id,
                TitleContentORM.child_title_id.label("title_id"),
                TitleContentORM.asset_id.label("asset_id"),
                (walk.c.depth + 1).label("depth"),
                (walk.c.ord + array([TitleContentORM.order_key])).label("ord"),
                (walk.c.seen + array([TitleContentORM.child_title_id])).label("seen"),
            )
            .select_from(walk)
            .join(TitleContentORM, TitleContentORM.parent_title_id == walk.c.title_id)
            # Only titles have contents; an asset row is a leaf.
            .where(walk.c.title_id.isnot(None))
            .where(walk.c.depth < max_depth)
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
