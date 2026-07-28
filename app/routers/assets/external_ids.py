# app/routers/assets/external_ids.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_external_identifier_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    ExternalIdentifierCreatePublic,
    ExternalIdentifierPatchPublic,
    ExternalIdentifierRead,
    ExternalIdentifierReadExtended,
)
from app.schemas.enums import EntityTypeEnum
from app.services import ExternalIdentifierService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/ids",
    response_model=list[ExternalIdentifierReadExtended],
    operation_id="list_asset_external_ids",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of asset external IDs retrieved successfully"},
    },
)
def get_asset_ids(
    asset_id: int, service: ExternalIdentifierService = Depends(get_external_identifier_service)
) -> list[ExternalIdentifierReadExtended]:
    return service.list_for_entity(EntityTypeEnum.asset, asset_id)


@router.post(
    "/{asset_id}/ids",
    response_model=ExternalIdentifierRead,
    operation_id="create_asset_external_id",
    status_code=201,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "External ID created successfully"},
        404: {"description": "Asset not found"},
    },
)
def create_asset_id(
    asset_id: int,
    ref: ExternalIdentifierCreatePublic,
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> ExternalIdentifierRead:
    return service.create_for_entity(EntityTypeEnum.asset, asset_id, ref)


@router.patch(
    "/{asset_id}/ids/{record_id}",
    response_model=ExternalIdentifierRead,
    operation_id="update_asset_external_id",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "External ID updated successfully"},
        404: {"description": "External ID not found or doesn't belong to this asset"},
    },
)
def update_asset_id(
    asset_id: int,
    record_id: int,
    update: ExternalIdentifierPatchPublic,
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> ExternalIdentifierRead:
    return service.update_for_entity(EntityTypeEnum.asset, asset_id, record_id, update)


@router.delete(
    "/{asset_id}/ids/{record_id}",
    operation_id="delete_asset_external_id",
    status_code=204,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "External ID deleted successfully"},
        404: {"description": "External ID not found or doesn't belong to this asset"},
    },
)
def delete_asset_id(
    asset_id: int,
    record_id: int,
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> None:
    return service.delete_for_entity(EntityTypeEnum.asset, asset_id, record_id)
