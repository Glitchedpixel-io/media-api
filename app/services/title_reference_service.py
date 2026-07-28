# app/title_reference_service.py
from fastapi import HTTPException

from app.repositories import TitleReferenceRepository, TitleRepository
from app.schemas import (
    TitleReferenceCreateInternal,
    TitleReferenceCreatePublic,
    TitleReferencePatchPublic,
    TitleReferenceRead,
    TitleReferenceUpdateInternal,
)
from app.services.errors import translate_repository_errors


class TitleReferenceService:
    def __init__(
        self, title_repository: TitleRepository, repository: TitleReferenceRepository
    ) -> None:
        self.title_repository = title_repository
        self.repository = repository

    @translate_repository_errors
    def create_reference(
        self, title_id: int, reference: TitleReferenceCreatePublic
    ) -> TitleReferenceRead:
        return self.repository.create(
            TitleReferenceCreateInternal(title_id=title_id, **reference.model_dump())
        )

    def get_title_references(self, title_id: int) -> list[TitleReferenceRead]:
        if not self.title_repository.exists(title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.repository.list_title_references(title_id)

    @translate_repository_errors(not_found_message="Title Reference not found")
    def update_title_reference(
        self,
        title_id: int,
        title_reference_id: int,
        update: TitleReferencePatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> TitleReferenceRead:
        return self.repository.update(
            title_reference_id,
            TitleReferenceUpdateInternal(
                title_id=title_id,
                **update.model_dump(exclude_none=exclude_none),  # type: ignore
            ),
        )
