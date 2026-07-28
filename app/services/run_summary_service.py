# app/services/run_summary_service.py
from app.repositories import RunSummaryRepository, ScannerRunSummaryRepository
from app.schemas import (
    RunSummaryCreateInternal,
    RunSummaryCreatePublic,
    RunSummaryRead,
    ScannerRunSummaryCreateInternal,
    ScannerRunSummaryCreatePublic,
    ScannerRunSummaryRead,
)
from app.services.errors import translate_repository_errors


class RunSummaryService:
    def __init__(self, repository: RunSummaryRepository) -> None:
        self.repository = repository

    @translate_repository_errors
    def create_run_summary(self, run_summary: RunSummaryCreatePublic) -> RunSummaryRead:
        return self.repository.create(RunSummaryCreateInternal(**run_summary.model_dump()))


class ScannerRunSummaryService:
    def __init__(self, repository: ScannerRunSummaryRepository) -> None:
        self.repository = repository

    @translate_repository_errors
    def create_scanner_run_summary(
        self, run_summary: ScannerRunSummaryCreatePublic
    ) -> ScannerRunSummaryRead:
        return self.repository.create(ScannerRunSummaryCreateInternal(**run_summary.model_dump()))
