# app/routers/file_stream.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.dependencies import get_file_stream_service
from app.routers.base import QuietClientErrorRoute
from app.services import FileStreamService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get("/{asset_id}", operation_id="stream_asset")
def stream_asset(
    asset_id: int,
    range_header: str | None = Header(None, alias="Range"),
    service: FileStreamService = Depends(get_file_stream_service),
) -> StreamingResponse:
    result = service.build_stream(asset_id, range_header)
    return StreamingResponse(
        result.iterator,
        status_code=result.status_code,
        media_type=result.media_type,
        headers=result.headers,
    )
