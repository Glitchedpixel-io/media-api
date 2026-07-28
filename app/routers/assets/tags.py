# app/routers/assets/tags.py
from __future__ import annotations

from http import HTTPStatus
from fastapi import APIRouter, Depends, Response

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_tag_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import TaggingReport, TagNameSet, TagRead, TagSet
from app.services import TagService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/tags",
    response_model=list[TagRead],
    operation_id="list_asset_tags",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of asset tags retrieved successfully"},
    },
)
def get_asset_tags(
    asset_id: int,
    service: TagService = Depends(get_tag_service),
) -> list[TagRead]:
    return service.get_asset_tags(asset_id)


@router.put(
    "/{asset_id}/tags",
    response_model=list[TagRead],
    operation_id="set_asset_tags",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Asset tags updated successfully"},
    },
)
def tag_asset(
    asset_id: int,
    tag_ids: TagSet,
    service: TagService = Depends(get_tag_service),
) -> list[TagRead]:
    return service.tag_asset_with_tag_ids(asset_id, tag_ids)


@router.post(
    "/{asset_id}/tags",
    response_model=TaggingReport,
    operation_id="add_asset_tags_by_name",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {
            "description": "Asset tagged by name successfully, returns report of tags created/found"
        },
    },
)
def tag_asset_by_name(
    asset_id: int,
    tag_names: TagNameSet,
    service: TagService = Depends(get_tag_service),
) -> TaggingReport:
    return service.tag_asset_with_tag_names(asset_id, tag_names)


@router.delete(
    "/{asset_id}/tags/{tag_id}",
    operation_id="remove_asset_tag",
    responses={
        200: {"description": "Asset was untagged successfully"},
        204: {"description": "Asset exists, but did not have the tag"},
        404: {"description": "Asset does not exist"},
    },
)
def untag_asset_by_id(
    asset_id: int,
    tag_id: int,
    response: Response,
    service: TagService = Depends(get_tag_service),
) -> None:
    if not service.untag_asset(asset_id, tag_id):
        response.status_code = HTTPStatus.NO_CONTENT
