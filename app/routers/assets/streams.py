# app/routers/assets/streams.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_stream_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import StreamCreatePublic, StreamRead
from app.services import StreamService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/streams",
    response_model=list[StreamRead],
    operation_id="list_asset_streams",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of asset streams retrieved successfully"},
    },
)
def get_asset_streams(
    asset_id: int,
    service: StreamService = Depends(get_stream_service),
) -> list[StreamRead]:
    return service.get_asset_streams(asset_id)


@router.post(
    "/{asset_id}/streams",
    response_model=StreamRead,
    operation_id="create_asset_stream",
    status_code=201,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Stream created successfully"},
    },
)
def create_asset_stream(
    asset_id: int,
    stream: StreamCreatePublic,
    service: StreamService = Depends(get_stream_service),
) -> StreamRead:
    return service.create_stream(asset_id, stream)


@router.delete(
    "/{asset_id}/streams",
    operation_id="delete_asset_streams",
    status_code=204,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Streams deleted successfully"},
    },
)
def delete_asset_streams(
    asset_id: int,
    service: StreamService = Depends(get_stream_service),
) -> None:
    return service.delete_asset_streams(asset_id)
