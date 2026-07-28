# app/services/metadata_service.py
from __future__ import annotations

from fastapi import HTTPException

from app.repositories import MediaRepository, MetadataRepository
from app.schemas import (
    MetadataCreateInternal,
    MetadataCreatePublic,
    MetadataPatchPublic,
    MetadataRead,
    MetadataUpdateInternal,
)
from app.services.errors import domain_error_detail, translate_repository_errors


class MetadataService:
    def __init__(
        self,
        metadata_repository: MetadataRepository,
        media_repository: MediaRepository,
    ) -> None:
        self.repo = metadata_repository
        self.media_repo = media_repository

    # ----------------------- Asset metadata

    def get_asset_metadata(self, asset_id: int) -> list[MetadataRead]:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.get_asset_metadata(asset_id)

    def get_asset_metadata_item(self, asset_id: int, metadata_id: int) -> MetadataRead:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        item = self.repo.get(metadata_id)
        if item is None or item.asset_id != asset_id:
            raise HTTPException(status_code=404, detail="Metadata not found")
        return item

    @translate_repository_errors
    def create_asset_metadata(self, asset_id: int, metadata: MetadataCreatePublic) -> MetadataRead:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.create(MetadataCreateInternal(asset_id=asset_id, **metadata.model_dump()))

    @translate_repository_errors(not_found_message="Metadata not found")
    def update_asset_metadata(
        self,
        asset_id: int,
        metadata_id: int,
        update: MetadataPatchPublic,  # type: ignore
    ) -> MetadataRead:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")

        # Ensure the item exists and belongs to the asset
        existing = self.repo.get(metadata_id)
        if existing is None or existing.asset_id != asset_id:
            raise HTTPException(status_code=404, detail="Metadata not found")

        payload = update.model_dump(exclude_none=True)  # type: ignore
        # Do not allow changing asset association via update
        if "asset_id" in payload and payload["asset_id"] != asset_id:
            raise HTTPException(
                status_code=422, detail=domain_error_detail("asset_id cannot be changed")
            )

        return self.repo.update(
            metadata_id,
            MetadataUpdateInternal(**payload),
        )

    def delete_asset_metadata(self, asset_id: int, metadata_id: int) -> None:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        existing = self.repo.get(metadata_id)
        if existing is None or existing.asset_id != asset_id:
            raise HTTPException(status_code=404, detail="Metadata not found")
        # Silent delete as per repository behavior
        self.repo.delete(metadata_id)
