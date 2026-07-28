# app/routers/external_ids.py
"""
API endpoints for generic external identifier resolution.

This router provides the resolution endpoint that can resolve external IDs
to internal entities (assets or titles).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api_responses import COMMON_READ_RESPONSES
from app.dependencies import get_external_identifier_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import ExternalIdResolution
from app.services import ExternalIdentifierService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/resolve",
    response_model=ExternalIdResolution,
    operation_id="resolve_external_id",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "External ID resolved successfully"},
        404: {"description": "External ID not found"},
    },
)
def resolve_external_id(
    scheme: str = Query(..., description="Scheme code (e.g., 'imdb', 'tmdb')"),
    external_id: str = Query(..., description="External ID value"),
    service: ExternalIdentifierService = Depends(get_external_identifier_service),
) -> ExternalIdResolution:
    """
    Resolve an external ID to an internal entity.

    Returns the entity type (asset or title) and entity ID for the given
    external ID in the specified scheme.
    """
    return service.resolve(scheme, external_id)
