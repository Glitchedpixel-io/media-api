# app/repositories/external_identifier_repository.py
from sqlalchemy import select, delete
from sqlalchemy.orm import contains_eager

from app.models import ExternalIdentifierORM, IdSchemeORM
from app.schemas import (
    ExternalIdentifierCreateInternal,
    ExternalIdentifierRead,
    ExternalIdentifierReadExtended,
    ExternalIdentifierUpdateInternal,
)
from app.schemas.enums import EntityTypeEnum

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import ExternalIdentifierRepository

# Hard ceiling on a per-entity identifier list. Nothing bounds the fan-out: unlike
# the legacy asset table, external_identifiers is unique on (scheme_id, external_id)
# only, with no per-entity-per-scheme constraint, so one entity may carry any number
# of identifiers in a single scheme. The endpoint had no bound of any kind (#95).
# Restated in the 200 description of both /ids routes -- keep those in step.
MAX_IDENTIFIERS_PER_ENTITY = 500


class SQLAlchemyExternalIdentifierRepository(
    SQLAlchemyBaseRepository, ExternalIdentifierRepository
):
    """
    SQLAlchemy implementation of ExternalIdentifierRepository.

    Manages generic external identifiers for both assets and titles using
    the typed association pattern (entity_type + entity_id).
    """

    def resolve(self, scheme_id: int, external_id: str) -> tuple[EntityTypeEnum, int] | None:
        """
        Resolve an external ID to an entity.

        Returns (entity_type, entity_id) if found, None otherwise.
        """
        stmt = select(ExternalIdentifierORM.entity_type, ExternalIdentifierORM.entity_id).where(
            ExternalIdentifierORM.scheme_id == scheme_id,
            ExternalIdentifierORM.external_id == external_id,
        )
        result = self.db.execute(stmt).first()
        return (result[0], result[1]) if result else None

    def resolve_by_code(
        self, scheme_code: str, external_id: str
    ) -> tuple[EntityTypeEnum, int, int] | None:
        """
        Resolve an external ID by scheme code.

        Returns (entity_type, entity_id, scheme_id) if found, None otherwise.
        """
        stmt = (
            select(
                ExternalIdentifierORM.entity_type,
                ExternalIdentifierORM.entity_id,
                ExternalIdentifierORM.scheme_id,
            )
            .join(IdSchemeORM, ExternalIdentifierORM.scheme_id == IdSchemeORM.id)
            .where(
                IdSchemeORM.code == scheme_code,
                ExternalIdentifierORM.external_id == external_id,
            )
        )
        result = self.db.execute(stmt).first()
        return (result[0], result[1], result[2]) if result else None

    def get(self, record_id: int) -> ExternalIdentifierRead | None:
        """Get a single external identifier by ID."""
        orm = self.db.get(ExternalIdentifierORM, record_id)
        return ExternalIdentifierRead.model_validate(orm, from_attributes=True) if orm else None

    def list_for_entity(
        self, entity_type: EntityTypeEnum, entity_id: int
    ) -> list[ExternalIdentifierReadExtended]:
        """Get the external identifiers for a specific entity, bounded.

        At most ``MAX_IDENTIFIERS_PER_ENTITY`` rows are returned whatever the data
        holds, so the response size cannot be dictated by how many identifiers
        happen to be attached to one entity (#95).

        ``ExternalIdentifierReadExtended`` serialises ``scheme``, so the join is
        consumed via ``contains_eager``. The join was already here for the sort;
        without the eager load the relationship still lazy-loads once per row, which
        is the same N+1 shape as #49.

        Args:
            entity_type: Which kind of entity the identifiers are attached to.
            entity_id: The id of that entity.

        Returns:
            list[ExternalIdentifierReadExtended]: The identifiers, ordered by scheme
            label then id, capped at ``MAX_IDENTIFIERS_PER_ENTITY``.
        """
        stmt = (
            select(ExternalIdentifierORM)
            .join(IdSchemeORM, ExternalIdentifierORM.scheme_id == IdSchemeORM.id)
            .options(contains_eager(ExternalIdentifierORM.scheme))
            .where(
                ExternalIdentifierORM.entity_type == entity_type,
                ExternalIdentifierORM.entity_id == entity_id,
            )
            # id breaks ties on a non-unique label, so the capped window is stable
            .order_by(IdSchemeORM.label, ExternalIdentifierORM.id)
            .limit(MAX_IDENTIFIERS_PER_ENTITY)
        )
        return [
            ExternalIdentifierReadExtended.model_validate(orm, from_attributes=True)
            for orm in self.db.scalars(stmt).all()
        ]

    def create(self, ref: ExternalIdentifierCreateInternal) -> ExternalIdentifierRead:
        """Create a new external identifier."""
        orm = ExternalIdentifierORM(**ref.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return ExternalIdentifierRead.model_validate(orm, from_attributes=True)

    def update(
        self, record_id: int, update: ExternalIdentifierUpdateInternal
    ) -> ExternalIdentifierRead:
        """Update an existing external identifier."""
        orm = self.db.get(ExternalIdentifierORM, record_id)
        if not orm:
            raise NotFoundError

        update_data = update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return ExternalIdentifierRead.model_validate(orm, from_attributes=True)

    def delete(self, record_id: int) -> None:
        """Delete an external identifier."""
        stmt = delete(ExternalIdentifierORM).where(ExternalIdentifierORM.id == record_id)
        self.db.execute(stmt)
        self._safe_commit()
