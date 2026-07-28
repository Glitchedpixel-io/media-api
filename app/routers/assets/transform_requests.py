# app/routers/assets/transform_requests.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_transform_request_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import TransformRequestCreatePublic, TransformRequestRead
from app.services import TransformRequestService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/transform_requests",
    response_model=list[TransformRequestRead],
    operation_id="list_asset_transform_requests",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of asset transform requests retrieved successfully"},
    },
)
def get_asset_transform_requests(
    asset_id: int,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> list[TransformRequestRead]:
    return service.get_asset_transform_requests(asset_id)


@router.post(
    "/{asset_id}/transform_requests",
    response_model=TransformRequestRead,
    operation_id="create_asset_transform_request",
    status_code=201,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Transform request created successfully"},
    },
)
def create_asset_transform_request(
    asset_id: int,
    request: TransformRequestCreatePublic,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> TransformRequestRead:
    return service.create_asset_transform_request(asset_id, request)
