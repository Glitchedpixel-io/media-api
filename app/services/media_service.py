# app/services/media_service.py
import os
from pathlib import Path

from fastapi import HTTPException

from app.config import MediaConfig
from app.repositories import MediaRepository
from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    DuplicatePathError,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.schemas import (
    AssetCreateInternal,
    AssetCreatePublic,
    AssetListParams,
    AssetPatchPublic,
    AssetRead,
    AssetReadExtended,
    AssetUpdateInternal,
    PaginatedResponse,
)
from app.services.errors import domain_error_detail


class MediaService:
    def __init__(self, media_repository: MediaRepository, config: MediaConfig) -> None:
        self.repo = media_repository
        self.media_root = config.media_root

    def get_asset(self, asset_id: int) -> AssetReadExtended:
        asset = self.repo.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    def get_asset_by_external_id(self, scheme_id: int, external_id: str) -> AssetRead:
        asset = self.repo.get_by_external_id(scheme_id, external_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return asset

    def get_derived_assets(self, asset_id: int) -> list[AssetRead]:
        asset = self.repo.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.list_derived_assets(asset_id)

    def add_derived_asset(self, asset_id: int, child_asset_id: int) -> AssetRead:
        if not (self.repo.exists(asset_id) and self.repo.exists(child_asset_id)):
            raise HTTPException(status_code=404, detail="Asset not found")
        try:
            return self.repo.update(child_asset_id, AssetUpdateInternal(master_asset_id=asset_id))  # type: ignore
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail="Asset not found") from e
        except CheckViolation as e:
            raise HTTPException(status_code=409, detail="Relationship not permitted") from e
        except UniqueViolation as e:
            raise HTTPException(status_code=409, detail="Unique constraint violated.") from e
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e
        except (
            ForeignKeyViolation,
            NotNullViolation,
            EnumViolation,
            ConstraintViolation,
        ) as e:
            # Choose 400 or 422 depending on policy
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e

    def get_assets(
        self,
        params: AssetListParams,
    ) -> PaginatedResponse[AssetReadExtended]:
        """List assets.

        Raises:
            HTTPException: 422 if `sort` names a field the endpoint does not
                support. `normalize_sort` raises `EnumViolation` for that, and
                without translation it escaped as a 500 -- so a caller asking for an
                unsupported sort was told the server had failed. The route class
                cannot help: it only converts an `HTTPException` that already
                carries the right status.
        """
        try:
            return self.repo.list_paged(params)
        except EnumViolation as e:
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e

    def mark_assets_seen(self, ids: list[int]) -> int:
        """
        Bulk-update assets by setting last_seen to the current server time.

        Returns the number of rows affected. Empty input results in 0 and no write.
        Maps database lock to 423 status code via HTTPException.
        """
        if not ids:
            return 0
        try:
            return self.repo.mark_assets_seen(ids)
        except DatabaseLocked as e:
            raise HTTPException(
                status_code=423, detail="Database is currently in read-only mode"
            ) from e

    def create_asset(self, asset: AssetCreatePublic) -> AssetRead:
        try:
            return self.repo.create(AssetCreateInternal(**asset.model_dump()))
        except DuplicatePathError as e:
            raise HTTPException(
                status_code=409, detail="Asset with this path already exists."
            ) from e
        except UniqueViolation as e:
            raise HTTPException(status_code=409, detail="Unique constraint violated.") from e
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
            # Choose 400 or 422 depending on policy
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e

    def update_asset(
        self,
        asset_id: int,
        asset_update: AssetPatchPublic,  # type: ignore
        exclude_none: bool = True,
        perform_rename: bool = False,
    ) -> AssetRead:
        update_data = asset_update.model_dump(exclude_none=exclude_none)  # type: ignore

        # If perform_rename is True and path is being updated, handle the file rename/move
        if perform_rename and "path" in update_data:
            # Get the current asset to retrieve its current path
            current_asset = self.repo.get(asset_id, with_master_asset=False)
            if current_asset is None:
                raise HTTPException(status_code=404, detail="Asset not found!")

            # Get media root and resolve paths (use injected media_root or fall back to settings)
            media_root_str = self.media_root
            media_root = Path(media_root_str).resolve()

            new_rel_path = update_data["path"]
            new_filename = update_data.get("filename")

            # Validation 1: Ensure filename is the final part of the path
            expected_filename = Path(new_rel_path).name
            if new_filename is None:
                # If filename not provided, extract it from the path
                new_filename = expected_filename
                update_data["filename"] = new_filename
            elif new_filename != expected_filename:
                raise HTTPException(
                    status_code=422,
                    detail=domain_error_detail(
                        f"Filename '{new_filename}' does not match the final part of path '{new_rel_path}'"
                    ),
                )

            # Validation 2: Check that no other record has the new path
            if self.repo.path_exists(new_rel_path, exclude_asset_id=asset_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"Another asset already exists with path '{new_rel_path}'",
                )

            # Resolve absolute paths for file operations
            old_rel_path = current_asset.path
            old_abs_path = media_root / old_rel_path
            new_abs_path = media_root / new_rel_path

            # Validation 3: Check that the new path doesn't already exist on disk
            if os.path.exists(new_abs_path):
                raise HTTPException(
                    status_code=409, detail=f"File already exists at path '{new_rel_path}'"
                )

            # Validation 4: Check that the current file exists
            if not os.path.exists(old_abs_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"Source file not found at path '{old_rel_path}' {old_abs_path}",
                )

            # Perform the rename/move operation
            try:
                # Ensure the target directory exists
                new_abs_path.parent.mkdir(parents=True, exist_ok=True)

                # Perform the rename/move
                os.rename(old_abs_path, new_abs_path)
            except OSError as e:
                raise HTTPException(
                    status_code=500, detail=f"Failed to rename/move file: {e!s}"
                ) from e

        # Update the database record
        try:
            return self.repo.update(
                asset_id,
                AssetUpdateInternal(**update_data),
            )
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail="Asset not found") from e
        except DuplicatePathError as e:
            raise HTTPException(
                status_code=409, detail="Asset with this path already exists."
            ) from e
        except UniqueViolation as e:
            raise HTTPException(status_code=409, detail="Unique constraint violated.") from e
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
            # Choose 400 or 422 depending on policy
            raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e
