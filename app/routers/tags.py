# app/routers/tags.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_tag_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    PaginatedResponse,
    TagCreatePublic,
    TagListParams,
    TagPatchPublic,
    TagRead,
)
from app.services import TagService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "",
    response_model=PaginatedResponse[TagRead],
    operation_id="list_tags",
)
def get_tags(
    params: TagListParams = Depends(),
    service: TagService = Depends(get_tag_service),
) -> PaginatedResponse[TagRead]:
    # Returns top-level tags (i.e. those without a parent)
    return service.get_tags(params=params, parent_id=None)


@router.get(
    "/{tag_id}/tags",
    response_model=PaginatedResponse[TagRead],
    operation_id="list_child_tags",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of child tags retrieved successfully"},
    },
)
def get_child_tags(
    tag_id: int,
    params: TagListParams = Depends(),
    service: TagService = Depends(get_tag_service),
) -> PaginatedResponse[TagRead]:
    # Returns tags under the specified parent tag
    return service.get_tags(params=params, parent_id=tag_id)


@router.get(
    "/{tag_id}",
    response_model=TagRead,
    operation_id="get_tag",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Tag retrieved successfully"},
    },
)
def get_tag(tag_id: int, service: TagService = Depends(get_tag_service)) -> TagRead:
    return service.get_tag(tag_id)


@router.post(
    "",
    response_model=TagRead,
    status_code=201,
    operation_id="create_tag",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Tag created successfully"},
    },
)
def create_tag(tag: TagCreatePublic, service: TagService = Depends(get_tag_service)) -> TagRead:
    return service.create_tag(tag, parent_id=None)


@router.post(
    "/{tag_id}/tags",
    response_model=TagRead,
    status_code=201,
    operation_id="create_child_tag",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Child tag created successfully"},
    },
)
def create_child_tag(
    tag_id: int, tag: TagCreatePublic, service: TagService = Depends(get_tag_service)
) -> TagRead:
    return service.create_tag(tag=tag, parent_id=tag_id)


@router.patch(
    "/{tag_id}",
    response_model=TagRead,
    operation_id="update_tag",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Tag updated successfully"},
    },
)
def update_tag(
    tag_id: int,
    tag: TagPatchPublic,  # type: ignore
    service: TagService = Depends(get_tag_service),
) -> TagRead:
    return service.update_tag(tag_id, tag, True)


@router.put(
    "/{tag_id}",
    response_model=TagRead,
    operation_id="replace_tag",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Tag replaced successfully"},
    },
)
def update_tag_by_put(
    tag_id: int,
    tag: TagPatchPublic,  # type: ignore
    service: TagService = Depends(get_tag_service),
) -> TagRead:
    return service.update_tag(tag_id, tag, False)
