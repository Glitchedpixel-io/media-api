# app/routers/run_summaries.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_WRITE_RESPONSES
from app.dependencies import get_run_summary_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import RunSummaryCreatePublic, RunSummaryRead
from app.services import RunSummaryService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.post(
    "",
    response_model=RunSummaryRead,
    status_code=201,
    operation_id="create_run_summary",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Run summary created successfully"},
    },
)
def create_run_summary(
    run_summary: RunSummaryCreatePublic,
    service: RunSummaryService = Depends(get_run_summary_service),
) -> RunSummaryRead:
    return service.create_run_summary(run_summary)
