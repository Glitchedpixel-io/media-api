# app/routers/titles/contents.py
from fastapi import APIRouter, Depends, Query

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_title_content_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    TitleContentInsert,
    TitleContentPatchPublic,
    TitleContentRead,
    TitleContentReadExtended,
)
from app.services import TitleContentService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{parent_title_id}/contents",
    response_model=list[TitleContentReadExtended],
    operation_id="list_title_contents",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of title contents retrieved successfully"},
    },
)
def read_title_contents(
    parent_title_id: int,
    service: TitleContentService = Depends(get_title_content_service),
) -> list[TitleContentReadExtended] | None:
    return service.get_title_content(parent_title_id)


@router.post(
    "/{parent_title_id}/contents",
    response_model=TitleContentRead,
    status_code=201,
    operation_id="create_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title content created successfully"},
    },
)
def link_title_contents(
    parent_title_id: int,
    contents: TitleContentInsert,
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    return service.insert_positioned(parent_title_id, contents, position="end")


@router.patch(
    "/{parent_title_id}/contents/{title_contents_id}",
    response_model=TitleContentRead,
    operation_id="update_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title content updated successfully"},
    },
)
def partial_title_content_update(
    parent_title_id: int,
    title_contents_id: int,
    update: TitleContentPatchPublic,  # type: ignore
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    return service.update_title_content(
        parent_title_id=parent_title_id,
        title_contents_id=title_contents_id,
        update=update,
        exclude_none=True,
    )


@router.delete(
    "/{parent_title_id}/contents/{title_contents_id}",
    status_code=204,
    operation_id="delete_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Title content deleted successfully"},
    },
)
def unlink_title_contents(
    parent_title_id: int,
    title_contents_id: int,
    service: TitleContentService = Depends(get_title_content_service),
) -> None:
    return service.unlink_content(parent_title_id, title_contents_id)


@router.post(
    "/{parent_title_id}/contents/positioned",
    response_model=TitleContentRead,
    status_code=201,
    operation_id="create_title_content_positioned",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title content created with position successfully"},
    },
)
def create_title_content_positioned(
    parent_title_id: int,
    payload: TitleContentInsert,
    before_id: int | None = Query(None, description="Place before this id"),
    after_id: int | None = Query(None, description="Place after this id"),
    position: str | None = Query(None, description="Special position: 'start' or 'end'"),
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    return service.insert_positioned(
        parent_title_id,
        payload,
        before_id=before_id,
        after_id=after_id,
        position=position,
    )


@router.patch(
    "/{parent_title_id}/contents/{title_contents_id}/reorder",
    response_model=TitleContentRead,
    operation_id="reorder_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title content reordered successfully"},
    },
)
def reorder_title_content(
    parent_title_id: int,
    title_contents_id: int,
    before_id: int | None = Query(None, description="Place before this id"),
    after_id: int | None = Query(None, description="Place after this id"),
    position: str | None = Query(None, description="Special position: 'start' or 'end'"),
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    return service.reorder_content(
        parent_title_id,
        title_content_id=title_contents_id,
        before_id=before_id,
        after_id=after_id,
        position=position,
    )
