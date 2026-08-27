# app/routers/inbox.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api_responses import COMMON_WRITE_RESPONSES
from app.dependencies import (
    get_inbox_service,
)
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    AssetRead,
)
from app.schemas.inbox import InboxDeleteRequest, InboxImportRequest, InboxItem
from app.services.inbox_service import InboxService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "",
    response_model=list[InboxItem],
    operation_id="list_inbox",
)
def list_inbox(
    depth: int | None = Query(
        None,
        ge=1,
        description=(
            "How many levels of the tree to walk. Omit for the maximum. A directory "
            "left unwalked is returned with children_truncated set and null children, "
            "which is not the same as being empty."
        ),
    ),
    service: InboxService = Depends(get_inbox_service),
) -> list[InboxItem]:
    return service.list_inbox(depth)


@router.post(
    "",
    status_code=201,
    operation_id="import_from_inbox",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "File imported from inbox successfully"},
    },
)
def import_from_inbox(
    payload: InboxImportRequest,
    service: InboxService = Depends(get_inbox_service),
) -> AssetRead:
    return service.import_file(payload)


@router.delete(
    "",
    status_code=204,
    operation_id="delete_from_inbox",
    responses={
        **COMMON_WRITE_RESPONSES,
        204: {"description": "File deleted from inbox successfully"},
    },
)
def delete_from_inbox(
    payload: InboxDeleteRequest = Depends(),
    service: InboxService = Depends(get_inbox_service),
) -> None:
    return service.delete(payload)
