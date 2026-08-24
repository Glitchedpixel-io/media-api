# app/services/inbox_service.py
from __future__ import annotations

from datetime import UTC, datetime

import logfire
from fastapi import HTTPException

from app.repositories import (
    InboxRepository,
    MediaRepository,
    TransformRequestRepository,
)
from app.repositories.errors import ForbiddenError, NotFoundError
from app.schemas import (
    AssetCreateInternal,
    AssetRead,
    InboxImportRequest,
    TransformRequestCreateInternal,
)
from app.schemas.inbox import InboxDeleteRequest, InboxItem


class InboxService:
    def __init__(
        self,
        inbox_repository: InboxRepository,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        self.inbox = inbox_repository
        self.assets = media_repository
        self.transforms = transform_request_repository

    def list_inbox(self) -> list[InboxItem]:
        return self.inbox.list_all()

    def delete(self, item: InboxDeleteRequest) -> None:
        try:
            self.inbox.delete(item)
        except NotFoundError as nfe:
            raise HTTPException(status_code=404, detail="Inbox item not found") from nfe
        except ForbiddenError as fe:
            raise HTTPException(status_code=403, detail="Forbidden") from fe
        except Exception as e:
            logfire.exception(f"Unexpected error deleting inbox item {item.source}, with {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

    def import_file(self, item: InboxImportRequest) -> AssetRead:
        try:
            abs_asset_path, rel_asset_path = self.inbox.move(item)
            st = abs_asset_path.stat()
            creation_request = AssetCreateInternal(
                path=rel_asset_path.as_posix(),
                filename=rel_asset_path.name,
                duration=0,
                bitrate=0,
                container_format=None,
                size=st.st_size,
                mtime=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                last_seen=datetime.now(UTC),
                master_asset_id=None,
            )
            asset = self.assets.create(creation_request)  # todo: handle domain errors
            request = TransformRequestCreateInternal(
                asset_id=asset.id,
                transform_type="prefect.stream_reader",
                parameters={},
                actioned=False,
                worker_notes=None,
                worker=None,
                processed_at=None,
                outcome=None,
                duration=None,
                parent_transform_request_id=None,
            )
            self.transforms.create(request)  # todo: handle domain errors
            request_probe = TransformRequestCreateInternal(
                asset_id=asset.id,
                transform_type="prefect.ffprobe_metadata",
                # The consumer picks a payload schema by "schema_id" and rejects the whole
                # payload without one; the recognised field is "categories", not
                # "metadata_types". Getting either wrong meant the categories below were
                # discarded and the consumer's own defaults used for every request (#35).
                parameters={"schema_id": "probe@1", "categories": ["format", "chapters"]},
                actioned=False,
                worker_notes=None,
                worker=None,
                processed_at=None,
                outcome=None,
                duration=None,
                parent_transform_request_id=None,
            )
            self.transforms.create(request_probe)  # todo: handle domain errors
            return asset
        except NotFoundError as nfe:
            raise HTTPException(status_code=404, detail="Inbox item not found") from nfe
        except ForbiddenError as fe:
            raise HTTPException(status_code=403, detail="Forbidden") from fe
        except Exception as e:
            logfire.exception(
                f"Unexpected error importing inbox file: {item.model_dump()}",
            )
            raise HTTPException(status_code=500, detail=str(e)) from e
