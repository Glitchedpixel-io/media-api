# app/title_service.py
from fastapi import HTTPException

from app.repositories import TitleRepository
from app.schemas import (
    PaginatedResponse,
    TitleCreateInternal,
    TitleCreatePublic,
    TitleListParams,
    TitlePatchPublic,
    TitleRead,
    TitleReadExtended,
    TitleUpdateInternal,
)
from app.services.errors import translate_repository_errors


class TitleService:
    def __init__(self, repository: TitleRepository) -> None:
        self.repository = repository

    def get_titles(
        self,
        params: TitleListParams,
    ) -> PaginatedResponse[TitleReadExtended]:
        return self.repository.list_paged(params)

    def get_title(self, title_id: int) -> TitleRead:
        title = self.repository.get(title_id)
        if title is None:
            raise HTTPException(status_code=404, detail="Title not found")
        return title

    def get_title_by_external_id(self, scheme_id: int, external_id: str) -> TitleRead:
        asset = self.repository.get_by_external_id(scheme_id, external_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Title not found")
        return asset

    @translate_repository_errors
    def create_title(self, title: TitleCreatePublic) -> TitleRead:
        return self.repository.create(TitleCreateInternal(**title.model_dump()))

    @translate_repository_errors(not_found_message="Title not found")
    def update_title(
        self,
        title_id: int,
        update: TitlePatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> TitleRead:
        return self.repository.update(
            title_id,
            TitleUpdateInternal(**update.model_dump(exclude_none=exclude_none)),  # type: ignore
        )
