# app/repositories/external_identifier_repository.py
from sqlalchemy import select, delete

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
        """Get all external identifiers for a specific entity."""
        stmt = (
            select(ExternalIdentifierORM)
            .join(IdSchemeORM, ExternalIdentifierORM.scheme_id == IdSchemeORM.id)
            .where(
                ExternalIdentifierORM.entity_type == entity_type,
                ExternalIdentifierORM.entity_id == entity_id,
            )
            .order_by(IdSchemeORM.label)
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
