# app/repositories/run_summary_repository.py
from sqlalchemy import select

from app.models import RunSummaryORM, ScannerRunSummaryORM
from app.schemas import (
    RunSummaryCreateInternal,
    RunSummaryRead,
    ScannerRunSummaryCreateInternal,
    ScannerRunSummaryRead,
)

from .base_repository import SQLAlchemyBaseRepository
from .protocols import RunSummaryRepository, ScannerRunSummaryRepository


class SQLAlchemyRunSummaryRepository(SQLAlchemyBaseRepository, RunSummaryRepository):
    def create(self, run_summary: RunSummaryCreateInternal) -> RunSummaryRead:
        orm = RunSummaryORM(**run_summary.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return RunSummaryRead.model_validate(orm)

    def get(self, run_summary_id: int) -> RunSummaryRead | None:
        orm = self.db.execute(
            select(RunSummaryORM).where(RunSummaryORM.id == run_summary_id)
        ).scalar_one_or_none()
        return RunSummaryRead.model_validate(orm) if orm else None

    def exists(self, run_summary_id: int) -> bool:
        return self.db.get(RunSummaryORM, run_summary_id) is not None


class SQLAlchemyScannerRunSummaryRepository(SQLAlchemyBaseRepository, ScannerRunSummaryRepository):
    def create(self, run_summary: ScannerRunSummaryCreateInternal) -> ScannerRunSummaryRead:
        orm = ScannerRunSummaryORM(**run_summary.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return ScannerRunSummaryRead.model_validate(orm)

    def get(self, run_summary_id: int) -> ScannerRunSummaryRead | None:
        orm = self.db.execute(
            select(ScannerRunSummaryORM).where(ScannerRunSummaryORM.id == run_summary_id)
        ).scalar_one_or_none()
        return ScannerRunSummaryRead.model_validate(orm) if orm else None

    def exists(self, run_summary_id: int) -> bool:
        return self.db.get(ScannerRunSummaryORM, run_summary_id) is not None
