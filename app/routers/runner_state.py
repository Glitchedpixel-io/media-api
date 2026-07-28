# app/routers/runner_state.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_runner_state_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import RunnerStateCreatePublic, RunnerStatePatchPublic, RunnerStateRead
from app.services import RunnerStateService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{runner_key:str}",
    response_model=RunnerStateRead,
    operation_id="get_runner_state",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Runner state retrieved successfully"},
    },
)
def get_runner_state(
    runner_key: str, service: RunnerStateService = Depends(get_runner_state_service)
) -> RunnerStateRead:
    return service.get_runner_state(runner_key)


@router.post(
    "",
    response_model=RunnerStateRead,
    operation_id="create_runner_state",
    status_code=201,
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Runner state created successfully"},
    },
)
def create_runner_state(
    state: RunnerStateCreatePublic,
    service: RunnerStateService = Depends(get_runner_state_service),
) -> RunnerStateRead:
    return service.create_runner_state(state)


@router.patch(
    "/{runner_key:str}",
    response_model=RunnerStateRead,
    operation_id="update_runner_state",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Runner state updated successfully"},
    },
)
def partial_runner_update(
    runner_key: str,
    update: RunnerStatePatchPublic,
    service: RunnerStateService = Depends(get_runner_state_service),
) -> RunnerStateRead:
    return service.update_runner_state(runner_key, update, True)
