# app/routers/titles/tags.py

from http import HTTPStatus
from fastapi import APIRouter, Depends, Response

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_tag_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import TaggingReport, TagNameSet, TagRead, TagSet
from app.services import TagService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{title_id}/tags",
    response_model=list[TagRead],
    operation_id="list_title_tags",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of title tags retrieved successfully"},
    },
)
def get_title_tags(
    title_id: int,
    service: TagService = Depends(get_tag_service),
) -> list[TagRead]:
    return service.get_title_tags(title_id)


@router.put(
    "/{title_id}/tags",
    response_model=list[TagRead],
    operation_id="set_title_tags",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title tags updated successfully"},
    },
)
def tag_title(
    title_id: int,
    tag_ids: TagSet,
    service: TagService = Depends(get_tag_service),
) -> list[TagRead]:
    return service.tag_title_with_tag_ids(title_id, tag_ids)


@router.post(
    "/{title_id}/tags",
    response_model=TaggingReport,
    operation_id="add_title_tags_by_name",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {
            "description": "Title tagged by name successfully, returns report of tags created/found"
        },
    },
)
def tag_title_by_name(
    title_id: int,
    tag_names: TagNameSet,
    service: TagService = Depends(get_tag_service),
) -> TaggingReport:
    return service.tag_title_with_tag_names(title_id, tag_names)


@router.delete(
    "/{title_id}/tags/{tag_id}",
    operation_id="remove_title_tag",
    responses={
        200: {"description": "Title was untagged successfully"},
        204: {"description": "Title exists, but did not have the tag"},
        404: {"description": "Title does not exist"},
    },
)
def untag_title_by_id(
    title_id: int,
    tag_id: int,
    response: Response,
    service: TagService = Depends(get_tag_service),
) -> None:
    if not service.untag_title(title_id, tag_id):
        response.status_code = HTTPStatus.NO_CONTENT
