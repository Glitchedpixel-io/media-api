# app/routers/titles/external_ids.py
"""
API endpoints for managing external IDs on titles.
"""

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
    "/{title_id}/ids",
    response_model=list[ExternalIdentifierReadExtended],
    operation_id="list_title_external_ids",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of title external IDs retrieved successfully"},
    },
)
def get_title_ids(
    title_id: int, service: ExternalIdentifierService = Depends(get_external_identifier_service)
) -> list[ExternalIdentifierReadExtended]:
    """Get all external IDs for a title."""
    return service.list_for_entity(EntityTypeEnum.title, title_id)


@router.post(
    "/{title_id}/ids",
    response_model=ExternalIdentifierRead,
    operation_id="create_title_external_id",
    status_code=201,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "External ID created successfully"},
        404: {"description": "Title not found"},
    },
)
def create_title_id(
    title_id: int,
    ref: ExternalIdentifierCreatePublic,
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> ExternalIdentifierRead:
    """Create a new external ID for a title."""
    return service.create_for_entity(EntityTypeEnum.title, title_id, ref)


@router.patch(
    "/{title_id}/ids/{record_id}",
    response_model=ExternalIdentifierRead,
    operation_id="update_title_external_id",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "External ID updated successfully"},
        404: {"description": "External ID not found or doesn't belong to this title"},
    },
)
def update_title_id(
    title_id: int,
    record_id: int,
    update: ExternalIdentifierPatchPublic,
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> ExternalIdentifierRead:
    """Update an external ID for a title."""
    return service.update_for_entity(EntityTypeEnum.title, title_id, record_id, update)


@router.delete(
    "/{title_id}/ids/{record_id}",
    operation_id="delete_title_external_id",
    status_code=204,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "External ID deleted successfully"},
        404: {"description": "External ID not found or doesn't belong to this title"},
    },
)
def delete_title_id(
    title_id: int,
    record_id: int,
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> None:
    """Delete an external ID from a title."""
    return service.delete_for_entity(EntityTypeEnum.title, title_id, record_id)
