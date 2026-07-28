# app/routers/assets/core.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_media_service, get_title_content_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    AssetCreatePublic,
    AssetListParams,
    AssetPatchPublic,
    AssetRead,
    AssetReadExtended,
    AssetSeenBatch,
    PaginatedResponse,
    TitleContentReadParent,
)
from app.services import MediaService, TitleContentService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.post(
    "/",
    response_model=AssetRead,
    operation_id="create_asset",
    status_code=201,
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Asset created successfully"},
    },
)
def create_asset(
    asset: AssetCreatePublic, service: MediaService = Depends(get_media_service)
) -> AssetRead:
    return service.create_asset(asset)


@router.get(
    "/",
    response_model=PaginatedResponse[AssetReadExtended],
    operation_id="list_assets",
)
def read_assets(
    params: AssetListParams = Depends(),
    service: MediaService = Depends(get_media_service),
) -> PaginatedResponse[AssetReadExtended]:
    return service.get_assets(params)


@router.get(
    "/{asset_id:int}",
    response_model=AssetReadExtended,
    operation_id="get_asset",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Asset retrieved successfully"},
    },
)
def read_asset(
    asset_id: int,
    service: MediaService = Depends(get_media_service),
) -> AssetReadExtended:
    return service.get_asset(asset_id)


@router.get(
    "/{asset_id:int}/titles",
    response_model=list[TitleContentReadParent],
    operation_id="get_asset_titles",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Asset retrieved successfully"},
    },
)
def get_asset_titles(
    asset_id: int, service: TitleContentService = Depends(get_title_content_service)
) -> list[TitleContentReadParent]:
    return service.get_titles_with_asset(asset_id)


@router.get(
    "/by-scheme/{scheme_id}/{external_id}",
    response_model=AssetRead,
    operation_id="get_asset_by_external_id",
    responses={
        200: {"description": "Asset found"},
        404: {"description": "No asset with that scheme and external ID found"},
    },
)
def get_asset_by_external_id(
    scheme_id: int,
    external_id: str,
    service: MediaService = Depends(get_media_service),
) -> AssetRead:
    return service.get_asset_by_external_id(scheme_id, external_id)


@router.patch(
    "/{asset_id:int}",
    response_model=AssetRead,
    operation_id="update_asset",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Asset updated successfully"},
    },
)
def update_asset(
    asset_id: int,
    update: AssetPatchPublic,  # type: ignore
    perform_rename: bool = False,
    service: MediaService = Depends(get_media_service),
) -> AssetRead:
    return service.update_asset(asset_id, update, exclude_none=True, perform_rename=perform_rename)


@router.patch(
    "/seen",
    operation_id="mark_assets_seen",
    status_code=204,
    responses={
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Assets marked as seen successfully"},
    },
)
def mark_assets_seen(
    batch: AssetSeenBatch,
    service: MediaService = Depends(get_media_service),
) -> None:
    service.mark_assets_seen(batch.ids)
