# app/routers/title_types.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_title_type_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import TitleTypeCreatePublic, TitleTypePatchPublic, TitleTypeRead
from app.services import TitleTypeService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "",
    response_model=list[TitleTypeRead],
    operation_id="list_title_types",
)
def get_title_types(
    service: TitleTypeService = Depends(get_title_type_service),
) -> list[TitleTypeRead]:
    return service.get_title_types()


@router.get(
    "/{title_type_id}",
    response_model=TitleTypeRead,
    operation_id="get_title_type",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Title type retrieved successfully"},
    },
)
def get_title_type(
    title_type_id: int, service: TitleTypeService = Depends(get_title_type_service)
) -> TitleTypeRead:
    return service.get_title_type(title_type_id)


@router.post(
    "",
    response_model=TitleTypeRead,
    status_code=201,
    operation_id="create_title_type",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title type created successfully"},
    },
)
def create_title_type(
    title_type: TitleTypeCreatePublic,
    service: TitleTypeService = Depends(get_title_type_service),
) -> TitleTypeRead:
    return service.create_title_type(title_type)


@router.patch(
    "/{title_type_id}",
    response_model=TitleTypeRead,
    operation_id="update_title_type",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title type updated successfully"},
    },
)
def update_title_type(
    title_type_id: int,
    update: TitleTypePatchPublic,  # type: ignore
    service: TitleTypeService = Depends(get_title_type_service),
) -> TitleTypeRead:
    return service.update_title_type(title_type_id, update, True)


@router.delete(
    "/{title_type_id}",
    status_code=204,
    operation_id="delete_title_type",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Title type deleted successfully"},
        409: {"description": "Conflict - the title type is still used by one or more titles"},
    },
)
def delete_title_type(
    title_type_id: int,
    service: TitleTypeService = Depends(get_title_type_service),
) -> Response:
    service.delete_title_type(title_type_id)
    return Response(status_code=204)
