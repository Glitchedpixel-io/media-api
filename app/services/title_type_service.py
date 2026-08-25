# app/services/title_type_service.py
from __future__ import annotations

from fastapi import HTTPException

from app.repositories import TitleTypeRepository
from app.schemas import (
    TitleTypeCreateInternal,
    TitleTypeCreatePublic,
    TitleTypePatchPublic,
    TitleTypeRead,
    TitleTypeUpdateInternal,
)
from app.services.errors import translate_repository_errors


class TitleTypeService:
    """Service for managing the title types a title can be categorised as.

    Replaces the former ``title_type_enum``, so that adding, renaming, or
    removing a type is an API call rather than a migration (issue #41).
    """

    def __init__(self, repository: TitleTypeRepository) -> None:
        self.repo = repository

    def get_title_types(self) -> list[TitleTypeRead]:
        return self.repo.list_all()

    def get_title_type(self, title_type_id: int) -> TitleTypeRead:
        title_type = self.repo.get(title_type_id)
        if title_type is None:
            raise HTTPException(status_code=404, detail="Title type not found")
        return title_type

    @translate_repository_errors
    def create_title_type(self, title_type: TitleTypeCreatePublic) -> TitleTypeRead:
        internal = TitleTypeCreateInternal(**title_type.model_dump())
        return self.repo.create(internal)

    @translate_repository_errors(not_found_message="Title type not found")
    def update_title_type(
        self,
        title_type_id: int,
        update: TitleTypePatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> TitleTypeRead:
        internal = TitleTypeUpdateInternal(**update.model_dump(exclude_none=exclude_none))  # type: ignore
        return self.repo.update(title_type_id, internal)

    @translate_repository_errors(not_found_message="Title type not found")
    def delete_title_type(self, title_type_id: int) -> None:
        """Delete a title type that no title is using.

        The usage check is what produces a meaningful 409. Without it the
        ``ondelete="RESTRICT"`` foreign key still protects the data, but it
        surfaces as a ``ForeignKeyViolation``, which
        ``translate_repository_errors`` maps to 422 -- the wrong code for a
        resource that exists but is still referenced.

        Args:
            title_type_id: ID of the title type to delete.

        Raises:
            HTTPException: 404 if the type does not exist, or 409 if any title
                still references it.
        """
        if not self.repo.exists(title_type_id):
            raise HTTPException(status_code=404, detail="Title type not found")

        in_use = self.repo.usage_count(title_type_id)
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Title type is still used by {in_use} title(s) and cannot be deleted. "
                    "Reassign those titles to another type first."
                ),
            )
        self.repo.delete(title_type_id)
