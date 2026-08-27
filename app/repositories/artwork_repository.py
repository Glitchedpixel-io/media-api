# app/repositories/artwork_repository.py
from sqlalchemy import func, select

from app.models import ArtworkKindORM, ArtworkORM
from app.schemas import (
    ArtworkCreateInternal,
    ArtworkKindCreateInternal,
    ArtworkKindRead,
    ArtworkKindUpdateInternal,
    ArtworkRead,
    ArtworkUpdateInternal,
)
from app.schemas.enums import EntityTypeEnum

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import ArtworkKindRepository, ArtworkRepository


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
