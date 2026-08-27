# app/routers/titles/core.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_title_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    PaginatedResponse,
    TitleCreatePublic,
    TitleListParams,
    TitlePatchPublic,
    TitleRead,
    TitleReadExtended,
)
from app.services import TitleService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/",
    response_model=PaginatedResponse[TitleReadExtended],
    operation_id="list_titles",
)
def read_titles(
    params: TitleListParams = Depends(),
    service: TitleService = Depends(get_title_service),
) -> PaginatedResponse[TitleReadExtended]:
    return service.get_titles(params)


@router.get(
    "/{title_id}",
    response_model=TitleReadExtended,
    operation_id="get_title",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Title retrieved successfully"},
    },
)
def read_title(
    title_id: int, service: TitleService = Depends(get_title_service)
) -> TitleReadExtended:
    """One title, including the poster resolved from it or its contents.

    The response model widened from `TitleRead` to `TitleReadExtended`, which is a
    superset -- every field a caller already read is still there, with `poster` added.
    """
    return service.get_title(title_id)


@router.post(
    "/",
    response_model=TitleRead,
    status_code=201,
    operation_id="create_title",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title created successfully"},
    },
)
def create_title(
    title: TitleCreatePublic, service: TitleService = Depends(get_title_service)
) -> TitleRead:
    return service.create_title(title)


@router.patch(
    "/{title_id}",
    response_model=TitleRead,
    operation_id="update_title",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title updated successfully"},
    },
)
def partial_title_update(
    title_id: int,
    update: TitlePatchPublic,  # type: ignore
    service: TitleService = Depends(get_title_service),
) -> TitleRead:
    return service.update_title(title_id, update, True)


@router.put(
    "/{title_id}",
    response_model=TitleRead,
    operation_id="replace_title",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title replaced successfully"},
    },
)
def update_title(
    title_id: int,
    update: TitlePatchPublic,  # type: ignore
    service: TitleService = Depends(get_title_service),
) -> TitleRead:
    return service.update_title(title_id, update, False)


@router.get(
    "/by-scheme/{scheme_id}/{external_id}",
    response_model=TitleRead,
    operation_id="get_title_by_external_id",
    responses={
        200: {"description": "Title found"},
        404: {"description": "No title with that scheme and external ID found"},
    },
)
def get_title_by_external_id(
    scheme_id: int,
    external_id: str,
    service: TitleService = Depends(get_title_service),
) -> TitleRead:
    return service.get_title_by_external_id(scheme_id, external_id)
