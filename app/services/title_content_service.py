# app/title_content_service.py
from fastapi import HTTPException

from app.repositories import MediaRepository, TitleContentRepository, TitleRepository
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
    TitleContentInsert,
    TitleContentPatchPublic,
    TitleContentRead,
    TitleContentReadExtended,
    TitleContentReadParent,
    TitleContentUpdateInternal,
)
from app.services.errors import domain_error_detail, translate_repository_errors


class TitleContentService:
    def __init__(
        self,
        title_repository: TitleRepository,
        title_content_repository: TitleContentRepository,
        media_repository: MediaRepository,
    ) -> None:
        self.title_repository = title_repository
        self.title_content_repository = title_content_repository
        self.media_repository = media_repository

    def get_titles_with_asset(self, asset_id: int) -> list[TitleContentReadParent]:
        if not self.media_repository.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.title_content_repository.get_titles_with_asset(asset_id)

    @translate_repository_errors
    def insert_positioned(
        self,
        parent_title_id: int,
        insert: TitleContentInsert,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        position: str | None = None,
    ) -> TitleContentRead:
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.title_content_repository.create_positioned(  # type: ignore
            parent_title_id,
            insert,
            before_id=before_id,
            after_id=after_id,
            position=position,
        )

    def get_title_content(self, parent_title_id: int) -> list[TitleContentReadExtended]:
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        return self.title_content_repository.list_title_content(parent_title_id, True)

    @translate_repository_errors(not_found_message="Title Reference not found")
    def update_title_content(
        self,
        parent_title_id: int,
        title_contents_id: int,
        update: TitleContentPatchPublic,  # type: ignore
        exclude_none: bool,
    ) -> TitleContentRead:
        return self.title_content_repository.update(
            title_contents_id,
            TitleContentUpdateInternal(
                parent_title_id=parent_title_id,
                **update.model_dump(exclude_none=exclude_none),  # type: ignore
            ),
        )

    def reorder_content(
        self,
        parent_title_id: int,
        *,
        title_content_id: int,
        before_id: int | None = None,
        after_id: int | None = None,
        position: str | None = None,
    ) -> TitleContentRead:
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        try:
            updated = self.title_content_repository.reorder(
                parent_title_id,
                title_content_id,
                before_id=before_id,
                after_id=after_id,
                position=position,
            )
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail="Title Content not found") from e
        except UniqueViolation as e:
            raise HTTPException(status_code=409, detail="Unique constraint violated.") from e
        except (
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ) as e:
            # Choose 400 or 422 depending on policy
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except:
            raise HTTPException(status_code=500, detail="Internal server error")
        if not updated:
            raise HTTPException(status_code=404, detail="Title Content not found")
        else:
            return updated

    def unlink_content(self, parent_title_id: int, title_content_id: int) -> None:
        # First check if the title exists
        if not self.title_repository.exists(parent_title_id):
            raise HTTPException(status_code=404, detail="Title not found")
        try:
            self.title_content_repository.delete_title_content(title_content_id)
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except Exception:
            # Log the unexpected error for debugging
            # logger.error(f"Unexpected error deleting streams for asset {asset_id}: {e}")
            raise HTTPException(
                status_code=500, detail="Internal server error during content unlinking"
            )
