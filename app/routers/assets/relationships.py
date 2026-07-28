# app/routers/assets/relationships.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_media_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import AssetRead
from app.services import MediaService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/derived_assets",
    response_model=list[AssetRead],
    operation_id="list_derived_assets",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of derived assets retrieved successfully"},
    },
)
def read_derived_assets(
    asset_id: int,
    service: MediaService = Depends(get_media_service),
) -> list[AssetRead]:
    return service.get_derived_assets(asset_id)


@router.put(
    "/{asset_id}/derived_assets/{child_asset_id}",
    response_model=AssetRead,
    operation_id="add_derived_asset",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Derived asset relationship created successfully"},
    },
)
def declare_derived_asset(
    asset_id: int,
    child_asset_id: int,
    service: MediaService = Depends(get_media_service),
) -> AssetRead:
    return service.add_derived_asset(asset_id, child_asset_id)
