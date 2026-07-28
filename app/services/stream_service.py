# app/services/stream_service.py
from fastapi import HTTPException

from app.repositories.protocols import (
    MediaRepository,
    StreamRepository,
)
from app.schemas import (
    StreamCreateInternal,
    StreamCreatePublic,
    StreamPatchPublic,
    StreamRead,
    StreamUpdateInternal,
)
from app.services.errors import translate_repository_errors


class StreamService:
    def __init__(
        self, stream_repository: StreamRepository, media_repository: MediaRepository
    ) -> None:
        self.repo = stream_repository
        self.media_repo = media_repository

    def get_stream(self, stream_id: int) -> StreamRead:
        stream = self.repo.get(stream_id)
        if stream is None:
            raise HTTPException(status_code=404, detail="Stream not found")
        return stream

    def get_streams(self) -> list[StreamRead]:
        return self.repo.list_all()

    @translate_repository_errors
    def create_stream(self, asset_id: int, stream: StreamCreatePublic) -> StreamRead:
        return self.repo.create(StreamCreateInternal(asset_id=asset_id, **stream.model_dump()))

    @translate_repository_errors(not_found_message="Stream not found")
    def update_stream(
        self,
        stream_id: int,
        update: StreamPatchPublic,  # type: ignore
        exclude_none: bool = True,
    ) -> StreamRead:
        return self.repo.update(
            stream_id,
            StreamUpdateInternal(**update.model_dump(exclude_none=exclude_none)),  # type: ignore
        )

    def get_asset_streams(self, asset_id: int) -> list[StreamRead]:
        asset = self.media_repo.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.get_asset_streams(asset_id)

    @translate_repository_errors
    def delete_asset_streams(self, asset_id: int) -> None:
        if not self.media_repo.exists(asset_id):
            raise HTTPException(status_code=404, detail="Asset not found")
        return self.repo.delete_asset_streams(asset_id)
