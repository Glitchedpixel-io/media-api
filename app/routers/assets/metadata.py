# app/routers/assets/metadata.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_metadata_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import MetadataCreatePublic, MetadataPatchPublic, MetadataRead
from app.services import MetadataService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/metadata",
    response_model=list[MetadataRead],
    operation_id="list_asset_metadata",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of asset metadata items retrieved successfully"},
    },
)
def get_asset_metadata(
    asset_id: int, service: MetadataService = Depends(get_metadata_service)
) -> list[MetadataRead]:
    return service.get_asset_metadata(asset_id)


@router.get(
    "/{asset_id}/metadata/{metadata_id}",
    response_model=MetadataRead,
    operation_id="get_asset_metadata_item",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Metadata item retrieved successfully"},
    },
)
def get_asset_metadata_item(
    asset_id: int,
    metadata_id: int,
    service: MetadataService = Depends(get_metadata_service),
) -> MetadataRead:
    return service.get_asset_metadata_item(asset_id, metadata_id)


@router.post(
    "/{asset_id}/metadata",
    response_model=MetadataRead,
    operation_id="create_asset_metadata_item",
    status_code=201,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Metadata item created successfully"},
    },
)
def create_asset_metadata(
    asset_id: int,
    metadata: MetadataCreatePublic,
    service: MetadataService = Depends(get_metadata_service),
) -> MetadataRead:
    return service.create_asset_metadata(asset_id, metadata)


@router.delete(
    "/{asset_id}/metadata/{metadata_id}",
    operation_id="delete_asset_metadata_item",
    status_code=204,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Metadata item deleted successfully"},
    },
)
def delete_asset_metadata(
    asset_id: int,
    metadata_id: int,
    service: MetadataService = Depends(get_metadata_service),
) -> None:
    return service.delete_asset_metadata(asset_id, metadata_id)


@router.patch(
    "/{asset_id}/metadata/{metadata_id}",
    response_model=MetadataRead,
    operation_id="update_asset_metadata_item",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Metadata item updated successfully"},
    },
)
def update_asset_metadata(
    asset_id: int,
    metadata_id: int,
    update: MetadataPatchPublic,  # type: ignore
    service: MetadataService = Depends(get_metadata_service),
) -> MetadataRead:
    return service.update_asset_metadata(asset_id, metadata_id, update)
