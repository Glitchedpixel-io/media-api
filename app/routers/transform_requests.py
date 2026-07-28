# app/routers/transform_requests.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_transform_request_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    PaginatedResponse,
    TransformRequestClaim,
    TransformRequestCreatePublic,
    TransformRequestListParams,
    TransformRequestLogEntry,
    TransformRequestPatchPublic,
    TransformRequestRead,
    TransformRequestReadExpanded,
)
from app.services import TransformRequestService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "",
    response_model=PaginatedResponse[TransformRequestReadExpanded],
    operation_id="list_transform_requests",
)
def read_requests(
    params: TransformRequestListParams = Depends(),
    service: TransformRequestService = Depends(get_transform_request_service),
) -> PaginatedResponse[TransformRequestReadExpanded]:
    return service.get_transform_requests(params)


@router.get(
    "/{request_id}",
    response_model=TransformRequestRead,
    operation_id="get_transform_request",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Transform request retrieved successfully"},
    },
)
def read_request(
    request_id: int,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> TransformRequestRead:
    return service.get_transform_request(request_id)


@router.get(
    "/{request_id}/logs",
    response_model=list[TransformRequestLogEntry],
    operation_id="get_transform_request_logs",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Transform request logs retrieved successfully"},
    },
)
def read_request_logs(
    request_id: int,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> list[dict]:
    # The service returns plain dicts (see TransformRequestService.get_transform_request_logs);
    # `response_model` above documents and validates them against the public schema.
    return service.get_transform_request_logs(request_id)


@router.patch(
    "/{request_id}/retry",
    response_model=TransformRequestRead,
    operation_id="retry_transform_request",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Transform request retried successfully"},
    },
)
def retry_request(
    request_id: int,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> TransformRequestRead:
    return service.retry_transform_request(request_id)


@router.patch(
    "/{request_id}",
    response_model=TransformRequestRead,
    operation_id="update_transform_request",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Transform request updated successfully"},
    },
)
def update_request(
    request_id: int,
    update: TransformRequestPatchPublic,  # type: ignore
    service: TransformRequestService = Depends(get_transform_request_service),
) -> TransformRequestRead:
    return service.update_transform_request(request_id, update)


@router.patch(
    path="/{request_id}/heartbeat",
    operation_id="transform_request_heartbeat",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Heartbeat accepted and recorded"},
        400: {"description": "Heartbeat rejected"},
    },
    status_code=200,
)
def mark_heartbeat(
    request_id: int, service: TransformRequestService = Depends(get_transform_request_service)
) -> None:
    # mark the heartbeat for this task
    service.mark_heartbeat(request_id)


@router.post(
    "/claim",
    response_model=TransformRequestReadExpanded,
    operation_id="claim_transform_request",
    responses={
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Transform request claimed successfully"},
        204: {"description": "No requests available to claim"},
    },
)
def claim_next_request(
    claim: TransformRequestClaim,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> TransformRequestReadExpanded:
    return service.claim_next_request(
        transform_type=claim.transform_type,
        worker=claim.worker,
        external_job_id=claim.external_job_id,
    )


@router.post(
    "/{request_id}/link",
    response_model=TransformRequestRead,
    operation_id="create_linked_transform_request",
    status_code=201,
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Linked transform request created successfully"},
    },
)
def create_linked_request(
    request_id: int,
    request: TransformRequestCreatePublic,
    service: TransformRequestService = Depends(get_transform_request_service),
) -> TransformRequestRead:
    return service.create_linked_request(request_id, request)
