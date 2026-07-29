"""Unit tests for RunSummaryService and ScannerRunSummaryService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.repositories import RunSummaryRepository, ScannerRunSummaryRepository
from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotNullViolation,
    UniqueViolation,
)
from app.schemas import (
    RunSummaryCreateInternal,
    RunSummaryCreatePublic,
    ScannerRunSummaryCreateInternal,
    ScannerRunSummaryCreatePublic,
)
from app.services import RunSummaryService, ScannerRunSummaryService
from tests.factories import RunSummaryReadFactory

_CONSTRAINT_VIOLATIONS = [
    ForeignKeyViolation,
    NotNullViolation,
    CheckViolation,
    EnumViolation,
    ConstraintViolation,
]


def _public_summary() -> RunSummaryCreatePublic:
    return RunSummaryCreatePublic(
        worker_name="worker1",
        worker_type="system",
        transform_type="prefect.transcode",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        processed_count=3,
        success_count=2,
        failed_count=1,
        running_time=42,
        extras=None,
    )


def _public_scanner_summary() -> ScannerRunSummaryCreatePublic:
    return ScannerRunSummaryCreatePublic(
        worker_name="scanner1",
        worker_type="scanner",
        scan_path="/data/media",
        relative_to_path="/data",
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        running_time=99,
        dry_run=False,
        total_count=10,
        processed_count=8,
        folder_count=2,
        excluded_count=1,
        previously_seen_count=0,
        error_count=0,
        api_error_count=0,
        no_metadata_count=1,
        unsupported_file_count=0,
    )


@pytest.fixture
def repo() -> RunSummaryRepository:
    return create_autospec(RunSummaryRepository, instance=True, spec_set=True)


@pytest.fixture
def svc(repo: RunSummaryRepository) -> RunSummaryService:
    return RunSummaryService(repo)


@pytest.fixture
def scanner_repo() -> ScannerRunSummaryRepository:
    return create_autospec(ScannerRunSummaryRepository, instance=True, spec_set=True)


@pytest.fixture
def scanner_svc(scanner_repo: ScannerRunSummaryRepository) -> ScannerRunSummaryService:
    return ScannerRunSummaryService(scanner_repo)


class TestCreateRunSummary:
    @pytest.mark.unit
    def test_create_run_summary_success(self, repo, svc) -> None:
        created = RunSummaryReadFactory(id=1)
        repo.create.return_value = created

        result = svc.create_run_summary(_public_summary())

        assert result is created
        repo.create.assert_called_once()
        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, RunSummaryCreateInternal)
        assert call_arg.worker_name == "worker1"

    @pytest.mark.unit
    def test_create_run_summary_unique_violation(self, repo, svc) -> None:
        repo.create.side_effect = UniqueViolation("u")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_run_summary(_public_summary())

        assert exc_info.value.status_code == 409

    @pytest.mark.unit
    def test_create_run_summary_database_locked(self, repo, svc) -> None:
        repo.create.side_effect = DatabaseLocked("locked")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_run_summary(_public_summary())

        assert exc_info.value.status_code == 423

    @pytest.mark.unit
    @pytest.mark.parametrize("exc_class", _CONSTRAINT_VIOLATIONS)
    def test_create_run_summary_constraint_violations(self, exc_class, repo, svc) -> None:
        repo.create.side_effect = exc_class("c")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_run_summary(_public_summary())

        assert exc_info.value.status_code == 422


class TestCreateScannerRunSummary:
    @pytest.mark.unit
    def test_create_scanner_run_summary_success(self, scanner_repo, scanner_svc) -> None:
        sentinel = object()
        scanner_repo.create.return_value = sentinel

        result = scanner_svc.create_scanner_run_summary(_public_scanner_summary())

        assert result is sentinel
        call_arg = scanner_repo.create.call_args[0][0]
        assert isinstance(call_arg, ScannerRunSummaryCreateInternal)
        assert call_arg.scan_path == "/data/media"

    @pytest.mark.unit
    def test_create_scanner_run_summary_unique_violation(self, scanner_repo, scanner_svc) -> None:
        scanner_repo.create.side_effect = UniqueViolation("u")

        with pytest.raises(HTTPException) as exc_info:
            scanner_svc.create_scanner_run_summary(_public_scanner_summary())

        assert exc_info.value.status_code == 409

    @pytest.mark.unit
    def test_create_scanner_run_summary_database_locked(self, scanner_repo, scanner_svc) -> None:
        scanner_repo.create.side_effect = DatabaseLocked("locked")

        with pytest.raises(HTTPException) as exc_info:
            scanner_svc.create_scanner_run_summary(_public_scanner_summary())

        assert exc_info.value.status_code == 423

    @pytest.mark.unit
    @pytest.mark.parametrize("exc_class", _CONSTRAINT_VIOLATIONS)
    def test_create_scanner_run_summary_constraint_violations(
        self, exc_class, scanner_repo, scanner_svc
    ) -> None:
        scanner_repo.create.side_effect = exc_class("c")

        with pytest.raises(HTTPException) as exc_info:
            scanner_svc.create_scanner_run_summary(_public_scanner_summary())

        assert exc_info.value.status_code == 422
