# app/services/id_scheme_service.py
from __future__ import annotations

from fastapi import HTTPException

from app.repositories import ExternalIdentifierRepository, IdSchemeRepository
from app.schemas import (
    IdSchemeCreateInternal,
    IdSchemeCreatePublic,
    IdSchemePatchPublic,
    IdSchemeRead,
    IdSchemeUpdateInternal,
)
from app.services.errors import translate_repository_errors


class IdSchemeService:
    """
    Service for managing external ID schemes.

    Provides CRUD-like operations for ID schemes and maps repository errors to HTTP exceptions.
    Asset external ID methods are provided for backward compatibility and now use the
    generic external_identifiers table under the hood.
    """

    def __init__(
        self,
        repository: IdSchemeRepository,
        external_id_repo: ExternalIdentifierRepository,
    ) -> None:
        self.repo = repository
        self.external_id_repo = external_id_repo

    def get_schemes(self) -> list[IdSchemeRead]:
        return self.repo.list_all()

    def get_scheme(self, scheme_id: int) -> IdSchemeRead:
        scheme = self.repo.get(scheme_id)
        if scheme is None:
            raise HTTPException(status_code=404, detail="ID scheme not found")
        return scheme

    @translate_repository_errors
    def create_scheme(self, scheme: IdSchemeCreatePublic) -> IdSchemeRead:
        internal = IdSchemeCreateInternal(**scheme.model_dump())
        return self.repo.create(internal)

    @translate_repository_errors(not_found_message="ID scheme not found")
    def update_scheme(
        self, scheme_id: int, update: IdSchemePatchPublic, exclude_none: bool
    ) -> IdSchemeRead:
        internal = IdSchemeUpdateInternal(**update.model_dump(exclude_none=exclude_none))
        return self.repo.update(scheme_id, internal)
