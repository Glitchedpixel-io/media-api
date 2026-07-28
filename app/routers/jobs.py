# app/routers/jobs.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_WRITE_RESPONSES
from app.dependencies import get_job_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import JobRead
from app.services import JobService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.post(
    "",
    response_model=JobRead,
    status_code=201,
    operation_id="create_job",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Job created successfully"},
    },
)
def create_job(job_key: str, service: JobService = Depends(get_job_service)) -> JobRead:
    return service.create_job(job_key)


@router.put(
    "/{job_key}/heartbeat",
    response_model=JobRead,
    operation_id="update_job_heartbeat",
    responses={
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Job heartbeat updated successfully"},
    },
)
def mark_heartbeat(job_key: str, service: JobService = Depends(get_job_service)) -> JobRead:
    return service.mark_heartbeat(job_key)


@router.patch(
    "/{job_key}/completed",
    response_model=JobRead,
    operation_id="mark_job_completed",
    responses={
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Job marked as completed successfully"},
    },
)
def mark_completed(job_key: str, service: JobService = Depends(get_job_service)) -> JobRead:
    return service.mark_completed(job_key)
