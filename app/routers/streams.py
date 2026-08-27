# app/routers/streams.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_stream_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    PaginatedResponse,
    StreamListParams,
    StreamPatchPublic,
    StreamRead,
)
from app.services import StreamService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "",
    response_model=PaginatedResponse[StreamRead],
    operation_id="list_streams",
)
def read_streams(
    params: StreamListParams = Depends(),
    service: StreamService = Depends(get_stream_service),
) -> PaginatedResponse[StreamRead]:
    return service.get_streams(params)


@router.get(
    "/{stream_id}",
    response_model=StreamRead,
    operation_id="get_stream",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Stream retrieved successfully"},
    },
)
def read_stream(
    stream_id: int,
    service: StreamService = Depends(get_stream_service),
) -> StreamRead:
    return service.get_stream(stream_id)


@router.patch(
    "/{stream_id}",
    response_model=StreamRead,
    operation_id="update_stream",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Stream updated successfully"},
    },
)
def update_stream(
    stream_id: int,
    update: StreamPatchPublic,  # type: ignore
    service: StreamService = Depends(get_stream_service),
) -> StreamRead:
    return service.update_stream(stream_id, update, True)
