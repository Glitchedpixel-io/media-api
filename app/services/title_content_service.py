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

    def _reject_cycle(self, parent_title_id: int, child_title_id: int | None) -> None:
        """Refuse an edge that would make a title contain itself, directly or not.

        Containment is a DAG, and nothing in the schema can say so: Postgres cannot
        express reachability as a constraint, so the only declarative half is the
        self-edge case (``no_self_containment_chk``). The rest is here, in the same
        place the artwork service owns the integrity checks its own table cannot.

        A cycle is not a cosmetic problem. Any consumer walking containment for a
        breadcrumb or a tree hangs on one unless it carries its own defence -- the
        poster resolution has to, and that machinery exists only because this guard
        did not.

        409 rather than 422: the payload is well formed and the referenced titles both
        exist. What it conflicts with is the structure already stored, which is what
        409 is for.

        Args:
            parent_title_id: The title that would do the containing.
            child_title_id: The title that would be contained, or None for an asset
                entry, which cannot form a cycle because assets are leaves.

        Raises:
            HTTPException: 409 if the edge would close a containment cycle.
        """
        if child_title_id is None:
            return
        if child_title_id == parent_title_id:
            raise HTTPException(
                status_code=409,
                detail="A title cannot contain itself.",
            )
        if self.title_content_repository.can_reach(child_title_id, parent_title_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Title {child_title_id} already contains title {parent_title_id}, "
                    "directly or through its contents, so this would create a cycle."
                ),
            )

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
        self._reject_cycle(parent_title_id, insert.child_title_id)
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
        # A patch can repoint an existing row at a different child, which reaches the
        # same invalid state as inserting one. Guarding only the insert would leave
        # the shorter path to a cycle open.
        self._reject_cycle(parent_title_id, getattr(update, "child_title_id", None))
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
        except Exception as e:
            raise HTTPException(status_code=500, detail="Internal server error") from e
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
        except Exception as e:
            raise HTTPException(
                status_code=500, detail="Internal server error during content unlinking"
            ) from e
