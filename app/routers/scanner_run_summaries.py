# app/routers/scanner_run_summaries.py
from fastapi import APIRouter, Depends

from app.api_responses import COMMON_WRITE_RESPONSES
from app.dependencies import get_scanner_run_summary_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import ScannerRunSummaryCreatePublic, ScannerRunSummaryRead
from app.services import ScannerRunSummaryService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.post(
    "",
    response_model=ScannerRunSummaryRead,
    status_code=201,
    operation_id="create_scanner_run_summary",
    responses={
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Scanner run summary created successfully"},
    },
)
def create_scanner_run_summary(
    run_summary: ScannerRunSummaryCreatePublic,
    service: ScannerRunSummaryService = Depends(get_scanner_run_summary_service),
) -> ScannerRunSummaryRead:
    return service.create_scanner_run_summary(run_summary)
