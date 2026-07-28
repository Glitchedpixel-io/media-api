# app/routers/id_schemes.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_id_scheme_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import IdSchemeRead, IdSchemeCreatePublic, IdSchemePatchPublic
from app.services import IdSchemeService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "",
    response_model=list[IdSchemeRead],
    operation_id="list_id_schemes",
)
def get_id_schemes(service: IdSchemeService = Depends(get_id_scheme_service)) -> list[IdSchemeRead]:
    return service.get_schemes()


@router.get(
    "/{scheme_id}",
    response_model=IdSchemeRead,
    operation_id="get_id_scheme",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "ID scheme retrieved successfully"},
    },
)
def get_id_scheme(
    scheme_id: int, service: IdSchemeService = Depends(get_id_scheme_service)
) -> IdSchemeRead:
    return service.get_scheme(scheme_id)


@router.post(
    "",
    response_model=IdSchemeRead,
    status_code=201,
    operation_id="create_id_scheme",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "ID scheme created successfully"},
    },
)
def create_id_scheme(
    scheme: IdSchemeCreatePublic, service: IdSchemeService = Depends(get_id_scheme_service)
) -> IdSchemeRead:
    return service.create_scheme(scheme)


@router.patch(
    "/{scheme_id}",
    response_model=IdSchemeRead,
    operation_id="update_id_scheme",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "ID scheme updated successfully"},
    },
)
def update_scheme(
    scheme_id: int,
    update: IdSchemePatchPublic,
    service: IdSchemeService = Depends(get_id_scheme_service),
) -> IdSchemeRead:
    return service.update_scheme(scheme_id, update, True)
