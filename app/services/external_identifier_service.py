# app/services/external_identifier_service.py
from __future__ import annotations

from fastapi import HTTPException

from app.repositories import ExternalIdentifierRepository, MediaRepository, TitleRepository
from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.schemas import (
    ExternalIdentifierCreateInternal,
    ExternalIdentifierCreatePublic,
    ExternalIdentifierPatchPublic,
    ExternalIdentifierRead,
    ExternalIdentifierReadExtended,
    ExternalIdentifierUpdateInternal,
    ExternalIdResolution,
)
from app.schemas.enums import EntityTypeEnum
from app.services.errors import domain_error_detail, translate_repository_errors


class ExternalIdentifierService:
    """
    Service for managing generic external identifiers across assets and titles.

    Provides CRUD operations and resolution functionality with entity validation.
    """

    def __init__(
        self,
        external_id_repo: ExternalIdentifierRepository,
        media_repo: MediaRepository,
        title_repo: TitleRepository,
    ) -> None:
        self.external_id_repo = external_id_repo
        self.media_repo = media_repo
        self.title_repo = title_repo

    def resolve(self, scheme_code: str, external_id: str) -> ExternalIdResolution:
        """
        Resolve an external ID to an internal entity.

        Args:
            scheme_code: The scheme code (e.g., 'imdb', 'tmdb')
            external_id: The external ID value

        Returns:
            ExternalIdResolution with entity_type, entity_id, scheme_code, external_id

        Raises:
            HTTPException: 404 if not found
        """
        result = self.external_id_repo.resolve_by_code(scheme_code, external_id)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"External ID not found: {scheme_code}:{external_id}",
            )

        entity_type, entity_id, scheme_id = result
        return ExternalIdResolution(
            entity_type=entity_type,
            entity_id=entity_id,
            scheme_code=scheme_code,
            external_id=external_id,
        )

    def list_for_entity(
        self, entity_type: EntityTypeEnum, entity_id: int
    ) -> list[ExternalIdentifierReadExtended]:
        """Get all external IDs for a given entity."""
        return self.external_id_repo.list_for_entity(entity_type, entity_id)

    def create_for_entity(
        self,
        entity_type: EntityTypeEnum,
        entity_id: int,
        ref: ExternalIdentifierCreatePublic,
    ) -> ExternalIdentifierRead:
        """
        Create a new external ID for an entity.

        Validates that the entity exists before creating the external ID.

        Args:
            entity_type: Type of entity (asset or title)
            entity_id: ID of the entity
            ref: External ID creation data

        Returns:
            Created external identifier

        Raises:
            HTTPException: 404 if entity not found, 409 on unique violation, 422 on validation error
        """
        # Validate entity exists
        if not self._entity_exists(entity_type, entity_id):
            raise HTTPException(
                status_code=404,
                detail=f"{entity_type.value.capitalize()} not found: {entity_id}",
            )

        try:
            internal = ExternalIdentifierCreateInternal(
                entity_type=entity_type,
                entity_id=entity_id,
                **ref.model_dump(),
            )
            return self.external_id_repo.create(internal)
        except UniqueViolation as e:
            raise HTTPException(
                status_code=409,
                detail="Unique constraint violated. This external ID may already exist.",
            ) from e
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except (
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ) as e:
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e

    @translate_repository_errors(not_found_message="External ID not found")
    def update_for_entity(
        self,
        entity_type: EntityTypeEnum,
        entity_id: int,
        record_id: int,
        update: ExternalIdentifierPatchPublic,
    ) -> ExternalIdentifierRead:
        """
        Update an external ID.

        Validates that the record belongs to the specified entity.

        Args:
            entity_type: Type of entity (asset or title)
            entity_id: ID of the entity
            record_id: ID of the external identifier record
            update: Update data

        Returns:
            Updated external identifier

        Raises:
            HTTPException: 404 if not found, 409 on unique violation, 422 on validation error
        """
        # Verify the record exists and belongs to this entity
        existing = self.external_id_repo.get(record_id)
        if (
            existing is None
            or existing.entity_type != entity_type
            or existing.entity_id != entity_id
        ):
            raise NotFoundError

        internal = ExternalIdentifierUpdateInternal(**update.model_dump(exclude_none=True))
        return self.external_id_repo.update(record_id, internal)

    def delete_for_entity(
        self,
        entity_type: EntityTypeEnum,
        entity_id: int,
        record_id: int,
    ) -> None:
        """
        Delete an external ID.

        Validates that the record belongs to the specified entity.

        Args:
            entity_type: Type of entity (asset or title)
            entity_id: ID of the entity
            record_id: ID of the external identifier record

        Raises:
            HTTPException: 404 if not found or doesn't belong to entity
        """
        existing = self.external_id_repo.get(record_id)
        if (
            existing is None
            or existing.entity_type != entity_type
            or existing.entity_id != entity_id
        ):
            raise HTTPException(status_code=404, detail="External ID not found")

        self.external_id_repo.delete(record_id)

    def _entity_exists(self, entity_type: EntityTypeEnum, entity_id: int) -> bool:
        """Check if an entity exists."""
        if entity_type == EntityTypeEnum.asset:
            return self.media_repo.exists(entity_id)
        elif entity_type == EntityTypeEnum.title:
            return self.title_repo.exists(entity_id)
        else:
            return False
