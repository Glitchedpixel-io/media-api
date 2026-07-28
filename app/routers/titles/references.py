# app/routers/titles/references.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_title_reference_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    TitleReferenceCreatePublic,
    TitleReferencePatchPublic,
    TitleReferenceRead,
)
from app.services import TitleReferenceService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{title_id}/references",
    response_model=list[TitleReferenceRead],
    operation_id="list_title_references",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of title references retrieved successfully"},
    },
)
def read_title_references(
    title_id: int, service: TitleReferenceService = Depends(get_title_reference_service)
) -> list[TitleReferenceRead]:
    return service.get_title_references(title_id)


@router.post(
    "/{title_id}/references",
    response_model=TitleReferenceRead,
    status_code=201,
    operation_id="create_title_reference",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title reference created successfully"},
    },
)
def create_title_reference(
    title_id: int,
    title_reference: TitleReferenceCreatePublic,
    service: TitleReferenceService = Depends(get_title_reference_service),
) -> TitleReferenceRead:
    return service.create_reference(title_id, title_reference)


@router.patch(
    "/{title_id}/references/{reference_id}",
    response_model=TitleReferenceRead,
    operation_id="update_title_reference",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title reference updated successfully"},
    },
)
def partial_title_reference_update(
    title_id: int,
    reference_id: int,
    update: TitleReferencePatchPublic,  # type: ignore
    service: TitleReferenceService = Depends(get_title_reference_service),
) -> TitleReferenceRead:
    return service.update_title_reference(
        title_id=title_id,
        title_reference_id=reference_id,
        update=update,
        exclude_none=True,
    )
