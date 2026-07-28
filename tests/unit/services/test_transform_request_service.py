"""Unit tests for TransformRequestService."""

from __future__ import annotations

from unittest.mock import MagicMock, create_autospec

import pytest
from fastapi import HTTPException

from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    RecordCannotBeChanged,
    UniqueViolation,
)
from app.repositories.protocols import MediaRepository, TransformRequestRepository
from app.schemas import (
    OutcomeEnum,
    PageInfo,
    PaginatedResponse,
    TransformRequestCreateInternal,
    TransformRequestCreatePublic,
    TransformRequestListParams,
    TransformRequestPatchPublic,
    TransformRequestUpdateInternal,
)
from app.runners import JobDispatch, LogEntry, NullJobRunner
from app.services import TransformRequestService
from tests.factories import AssetReadFactory, TransformRequestReadFactory


class _RecordingRunner:
    """A minimal JobRunner double that records dispatches and returns canned logs."""

    def __init__(
        self,
        *,
        logs: list[LogEntry] | None = None,
        dispatch_error: Exception | None = None,
        dispatch_return: str | None = None,
    ) -> None:
        self._logs = logs or []
        self._dispatch_error = dispatch_error
        self._dispatch_return = dispatch_return
        self.dispatched: list[JobDispatch] = []
        self.fetched: list[str] = []

    def dispatch(self, job: JobDispatch) -> str | None:
        self.dispatched.append(job)
        if self._dispatch_error is not None:
            raise self._dispatch_error
        return self._dispatch_return

    def fetch_logs(self, external_ref: str) -> list[LogEntry]:
        self.fetched.append(external_ref)
        return list(self._logs)


@pytest.fixture
def tr_repo() -> TransformRequestRepository:
    return create_autospec(TransformRequestRepository, instance=True, spec_set=True)


@pytest.fixture
def m_repo() -> MediaRepository:
    return create_autospec(MediaRepository, instance=True, spec_set=True)


@pytest.fixture
def svc(tr_repo: TransformRequestRepository, m_repo: MediaRepository) -> TransformRequestService:
    return TransformRequestService(tr_repo, m_repo, NullJobRunner())


class TestGetTransformRequest:
    """Tests for TransformRequestService.get_transform_request."""

    @pytest.mark.unit
    def test_get_transform_request_success(self, tr_repo, m_repo, svc) -> None:
        """get_transform_request returns request when found in repository."""

        expected_request = TransformRequestReadFactory(
            id=123, asset_id=42, transform_type="prefect.transcode"
        )
        tr_repo.get.return_value = expected_request

        result = svc.get_transform_request(123)

        assert result is expected_request
        assert result.id == 123
        assert result.asset_id == 42
        assert result.transform_type == "prefect.transcode"
        tr_repo.get.assert_called_once_with(123)

    @pytest.mark.unit
    def test_get_transform_request_not_found(self, tr_repo, m_repo, svc) -> None:
        """get_transform_request raises 404 when repository returns None."""

        tr_repo.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.get_transform_request(999)

        assert exc_info.value.status_code == 404
        assert "Transform request not found" in exc_info.value.detail
        tr_repo.get.assert_called_once_with(999)

    @pytest.mark.unit
    def test_get_transform_request_with_various_ids(self, tr_repo, m_repo, svc) -> None:
        """get_transform_request correctly handles different request IDs."""

        test_ids = [1, 500, 999999]
        for request_id in test_ids:
            tr_repo.reset_mock()
            expected = TransformRequestReadFactory(id=request_id)
            tr_repo.get.return_value = expected

            result = svc.get_transform_request(request_id)

            assert result.id == request_id
            tr_repo.get.assert_called_once_with(request_id)


class TestGetTransformRequests:
    """Tests for TransformRequestService.get_transform_requests."""

    @pytest.mark.unit
    def test_get_transform_requests_with_default_params(self, tr_repo, m_repo, svc) -> None:
        """get_transform_requests delegates to repository with provided params."""

        requests = [TransformRequestReadFactory() for _ in range(3)]
        expected_response = PaginatedResponse(items=requests, page=PageInfo(next=None, prev=None))
        tr_repo.list_paged.return_value = expected_response

        params = TransformRequestListParams()
        result = svc.get_transform_requests(params)

        assert result is expected_response
        assert len(result.items) == 3
        assert result.page.next is None
        tr_repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_transform_requests_with_filters(self, tr_repo, m_repo, svc) -> None:
        """get_transform_requests passes filter parameters to repository."""

        tr_repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        params = TransformRequestListParams(
            transform_type="prefect.transcode",
            actioned=True,
            worker_assigned=False,
            outcome=OutcomeEnum.failed,
        )
        result = svc.get_transform_requests(params)

        assert result.items == []
        tr_repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_transform_requests_empty_result(self, tr_repo, m_repo, svc) -> None:
        """get_transform_requests returns empty list when no requests match."""

        tr_repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        params = TransformRequestListParams()
        result = svc.get_transform_requests(params)

        assert len(result.items) == 0

    @pytest.mark.unit
    def test_get_transform_requests_with_pagination(self, tr_repo, m_repo, svc) -> None:
        """get_transform_requests handles cursor pagination parameters correctly."""

        requests = [TransformRequestReadFactory() for _ in range(10)]
        tr_repo.list_paged.return_value = PaginatedResponse(
            items=requests, page=PageInfo(next="next_cursor", prev="prev_cursor")
        )

        params = TransformRequestListParams(limit=10, after="cursor123")
        result = svc.get_transform_requests(params)

        assert len(result.items) == 10
        assert result.page.next == "next_cursor"
        assert result.page.prev == "prev_cursor"


class TestUpdateTransformRequest:
    """Tests for TransformRequestService.update_transform_request."""

    @pytest.mark.unit
    def test_update_transform_request_success_with_exclude_none(self, tr_repo, m_repo, svc) -> None:
        """update_transform_request updates request with exclude_none=True."""

        updated_request = TransformRequestReadFactory(id=9, actioned=True)
        tr_repo.update.return_value = updated_request

        patch = TransformRequestPatchPublic(actioned=True)

        result = svc.update_transform_request(9, patch, exclude_none=True)

        assert result is updated_request
        assert result.id == 9
        assert result.actioned is True

        # Verify internal DTO
        tr_repo.update.assert_called_once()
        call_args = tr_repo.update.call_args[0]
        assert call_args[0] == 9
        assert isinstance(call_args[1], TransformRequestUpdateInternal)
        assert call_args[1].actioned is True

    @pytest.mark.unit
    def test_update_transform_request_success_without_exclude_none(
        self, tr_repo, m_repo, svc
    ) -> None:
        """update_transform_request updates request with exclude_none=False."""

        updated_request = TransformRequestReadFactory(id=9)
        tr_repo.update.return_value = updated_request

        patch = TransformRequestPatchPublic(actioned=True)

        result = svc.update_transform_request(9, patch, exclude_none=False)

        assert result is updated_request
        tr_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_transform_request_partial_update(self, tr_repo, m_repo, svc) -> None:
        """update_transform_request allows partial field updates."""

        tr_repo.update.return_value = TransformRequestReadFactory()

        patch = TransformRequestPatchPublic(outcome=OutcomeEnum.succeeded)

        svc.update_transform_request(5, patch, exclude_none=True)

        call_arg = tr_repo.update.call_args[0][1]
        assert hasattr(call_arg, "outcome")

    @pytest.mark.unit
    def test_update_transform_request_not_found(self, tr_repo, m_repo, svc) -> None:
        """update_transform_request raises 404 when request doesn't exist."""

        tr_repo.update.side_effect = NotFoundError("missing")

        patch = TransformRequestPatchPublic(actioned=True)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_transform_request(9, patch, exclude_none=True)

        assert exc_info.value.status_code == 404
        assert "Transform Request not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_transform_request_unique_violation(self, tr_repo, m_repo, svc) -> None:
        """update_transform_request raises 409 on unique constraint violation."""

        tr_repo.update.side_effect = UniqueViolation("u")

        patch = TransformRequestPatchPublic(actioned=True)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_transform_request(9, patch, exclude_none=False)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_transform_request_database_locked(self, tr_repo, m_repo, svc) -> None:
        """update_transform_request raises 423 when database is read-only."""

        tr_repo.update.side_effect = DatabaseLocked("locked")

        patch = TransformRequestPatchPublic(actioned=True)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_transform_request(9, patch, exclude_none=True)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_update_transform_request_constraint_violations(
        self, exc_class, tr_repo, m_repo, svc
    ) -> None:
        """update_transform_request raises 422 for various constraint violations."""

        tr_repo.update.side_effect = exc_class("c")

        patch = TransformRequestPatchPublic(actioned=True)

        with pytest.raises(HTTPException) as exc_info:
            svc.update_transform_request(9, patch, exclude_none=True)

        assert exc_info.value.status_code == 422


class TestCreateAssetTransformRequest:
    """Tests for TransformRequestService.create_asset_transform_request."""

    @pytest.mark.unit
    def test_create_asset_transform_request_success(self, tr_repo, m_repo, svc) -> None:
        """create_asset_transform_request creates request successfully."""

        created_request = TransformRequestReadFactory(
            id=1, asset_id=7, transform_type="prefect.transcode"
        )
        tr_repo.create.return_value = created_request

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")

        result = svc.create_asset_transform_request(7, payload)

        assert result is created_request
        assert result.asset_id == 7

        # Verify internal DTO has asset_id
        tr_repo.create.assert_called_once()
        call_arg = tr_repo.create.call_args[0][0]
        assert isinstance(call_arg, TransformRequestCreateInternal)
        assert call_arg.asset_id == 7
        assert call_arg.transform_type == "prefect.transcode"

    @pytest.mark.unit
    def test_create_asset_transform_request_unique_violation(self, tr_repo, m_repo, svc) -> None:
        """create_asset_transform_request raises 409 on unique constraint violation."""

        tr_repo.create.side_effect = UniqueViolation("u")

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_transform_request(7, payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_asset_transform_request_database_locked(self, tr_repo, m_repo, svc) -> None:
        """create_asset_transform_request raises 423 when database is read-only."""

        tr_repo.create.side_effect = DatabaseLocked("locked")

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_transform_request(7, payload)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_create_asset_transform_request_constraint_violations(
        self, exc_class, tr_repo, m_repo, svc
    ) -> None:
        """create_asset_transform_request raises 422 for various constraint violations."""

        tr_repo.create.side_effect = exc_class("c")

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_transform_request(7, payload)

        assert exc_info.value.status_code == 422


class TestCreateLinkedRequest:
    """Tests for TransformRequestService.create_linked_request."""

    @pytest.mark.unit
    def test_create_linked_request_success(self, tr_repo, m_repo, svc) -> None:
        """create_linked_request creates request linked to parent successfully."""

        parent_request = TransformRequestReadFactory(id=9, asset_id=11)
        tr_repo.get.return_value = parent_request
        created_request = TransformRequestReadFactory(
            id=10, asset_id=11, parent_transform_request_id=9
        )
        tr_repo.create.return_value = created_request

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")

        result = svc.create_linked_request(9, payload)

        assert result.asset_id == 11
        assert result.parent_transform_request_id == 9

        # Verify internal DTO structure
        call_arg = tr_repo.create.call_args[0][0]
        assert call_arg.asset_id == 11
        assert call_arg.parent_transform_request_id == 9

    @pytest.mark.unit
    def test_create_linked_request_parent_not_found(self, tr_repo, m_repo, svc) -> None:
        """create_linked_request raises 404 when parent doesn't exist."""

        tr_repo.get.return_value = None

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_linked_request(999, payload)

        assert exc_info.value.status_code == 404


class TestGetAssetTransformRequests:
    """Tests for TransformRequestService.get_asset_transform_requests."""

    @pytest.mark.unit
    def test_get_asset_transform_requests_success(self, tr_repo, m_repo, svc) -> None:
        """get_asset_transform_requests returns list of requests for asset."""

        m_repo.get.return_value = AssetReadFactory(id=11)
        requests = [TransformRequestReadFactory() for _ in range(3)]
        tr_repo.get_asset_transform_requests.return_value = requests

        result = svc.get_asset_transform_requests(11)

        assert isinstance(result, list)
        assert len(result) == 3
        m_repo.get.assert_called_once_with(11)
        tr_repo.get_asset_transform_requests.assert_called_once_with(11)

    @pytest.mark.unit
    def test_get_asset_transform_requests_empty_list(self, tr_repo, m_repo, svc) -> None:
        """get_asset_transform_requests returns empty list when no requests exist."""

        m_repo.get.return_value = AssetReadFactory(id=11)
        tr_repo.get_asset_transform_requests.return_value = []

        result = svc.get_asset_transform_requests(11)

        assert len(result) == 0

    @pytest.mark.unit
    def test_get_asset_transform_requests_asset_not_found(self, tr_repo, m_repo, svc) -> None:
        """get_asset_transform_requests raises 404 when asset doesn't exist."""

        m_repo.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_transform_requests(999)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.get.assert_called_once_with(999)
        tr_repo.get_asset_transform_requests.assert_not_called()


class TestRetryTransformRequest:
    """Tests for TransformRequestService.retry_transform_request."""

    @pytest.mark.unit
    def test_retry_transform_request_success(self, tr_repo, m_repo, svc) -> None:
        """retry_transform_request creates new request from actioned parent."""

        parent = TransformRequestReadFactory(
            id=5,
            actioned=True,
            outcome=OutcomeEnum.failed,
            asset_id=11,
            transform_type="prefect.transcode",
            parameters={"test": 122},
        )
        tr_repo.get.return_value = parent
        new_request = TransformRequestReadFactory(id=6, parent_transform_request_id=5)
        tr_repo.create.return_value = new_request

        result = svc.retry_transform_request(5)

        assert result is new_request

        # Verify internal DTO has cleared fields
        call_arg = tr_repo.create.call_args[0][0]
        assert call_arg.actioned is False
        assert call_arg.outcome is None
        assert call_arg.processed_at is None
        assert call_arg.worker is None
        assert call_arg.parent_transform_request_id == 5
        assert call_arg.transform_type == "prefect.transcode"
        assert call_arg.asset_id == 11

    @pytest.mark.unit
    def test_retry_transform_request_not_found(self, tr_repo, m_repo, svc) -> None:
        """retry_transform_request raises 404 when request doesn't exist."""

        tr_repo.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.retry_transform_request(5)

        assert exc_info.value.status_code == 404
        assert "Transform request not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_retry_transform_request_not_actioned(self, tr_repo, m_repo, svc) -> None:
        """retry_transform_request raises 409 when request not yet actioned."""

        parent = TransformRequestReadFactory(id=5, actioned=False)
        tr_repo.get.return_value = parent

        with pytest.raises(HTTPException) as exc_info:
            svc.retry_transform_request(5)

        assert exc_info.value.status_code == 409
        assert "not been actioned" in exc_info.value.detail


class TestMarkHeartbeat:
    """Tests for TransformRequestService.mark_heartbeat."""

    @pytest.mark.unit
    def test_mark_heartbeat_success(self, tr_repo, m_repo, svc) -> None:
        """mark_heartbeat updates heartbeat timestamp successfully."""

        # Should not raise an exception
        svc.mark_heartbeat(42)

        tr_repo.mark_heartbeat.assert_called_once_with(42)

    @pytest.mark.unit
    def test_mark_heartbeat_not_found(self, tr_repo, m_repo, svc) -> None:
        """mark_heartbeat raises 404 when request doesn't exist."""

        tr_repo.mark_heartbeat.side_effect = NotFoundError("missing")

        with pytest.raises(HTTPException) as exc_info:
            svc.mark_heartbeat(999)

        assert exc_info.value.status_code == 404
        assert "Transform Request not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_mark_heartbeat_database_locked(self, tr_repo, m_repo, svc) -> None:
        """mark_heartbeat raises 423 when database is read-only."""

        tr_repo.mark_heartbeat.side_effect = DatabaseLocked("locked")

        with pytest.raises(HTTPException) as exc_info:
            svc.mark_heartbeat(42)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    def test_mark_heartbeat_record_cannot_be_changed(self, tr_repo, m_repo, svc) -> None:
        """mark_heartbeat raises 400 when record cannot receive heartbeats."""

        tr_repo.mark_heartbeat.side_effect = RecordCannotBeChanged("cannot change")

        with pytest.raises(HTTPException) as exc_info:
            svc.mark_heartbeat(42)

        assert exc_info.value.status_code == 400
        assert "cannot receive heartbeats" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_mark_heartbeat_constraint_violations(self, exc_class, tr_repo, m_repo, svc) -> None:
        """mark_heartbeat raises 422 for various constraint violations."""

        tr_repo.mark_heartbeat.side_effect = exc_class("c")

        with pytest.raises(HTTPException) as exc_info:
            svc.mark_heartbeat(42)

        assert exc_info.value.status_code == 422


class TestClaimNextRequest:
    """Tests for TransformRequestService.claim_next_request."""

    @pytest.mark.unit
    def test_claim_next_request_success(self, tr_repo, m_repo, svc) -> None:
        """claim_next_request returns next available request for worker."""

        next_request = TransformRequestReadFactory(id=123)
        tr_repo.claim_next.return_value = next_request

        result = svc.claim_next_request("prefect.test", "worker1", None)

        assert result is next_request
        tr_repo.claim_next.assert_called_once_with(
            transform_type="prefect.test", worker="worker1", external_job_id=None
        )

    @pytest.mark.unit
    def test_claim_next_request_with_external_job_id(self, tr_repo, m_repo, svc) -> None:
        """claim_next_request passes external_job_id to repository."""

        next_request = TransformRequestReadFactory()
        tr_repo.claim_next.return_value = next_request

        result = svc.claim_next_request("prefect.transcode", "worker2", "flow-run-123")

        assert result is next_request
        tr_repo.claim_next.assert_called_once_with(
            transform_type="prefect.transcode",
            worker="worker2",
            external_job_id="flow-run-123",
        )

    @pytest.mark.unit
    def test_claim_next_request_no_tasks_available(self, tr_repo, m_repo, svc) -> None:
        """claim_next_request raises 204 when no requests available."""

        tr_repo.claim_next.side_effect = NotFoundError("none")

        with pytest.raises(HTTPException) as exc_info:
            svc.claim_next_request("prefect.transcode", "w1", None)

        assert exc_info.value.status_code == 204
        assert "No tasks available" in exc_info.value.detail

    @pytest.mark.unit
    def test_claim_next_request_database_locked(self, tr_repo, m_repo, svc) -> None:
        """claim_next_request raises 423 when database is read-only."""

        tr_repo.claim_next.side_effect = DatabaseLocked("locked")

        with pytest.raises(HTTPException) as exc_info:
            svc.claim_next_request("prefect.transcode", "w2", None)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    def test_claim_next_request_unexpected_exception(self, tr_repo, m_repo, svc) -> None:
        """claim_next_request raises 500 on unexpected exceptions."""

        tr_repo.claim_next.side_effect = RuntimeError("boom")

        with pytest.raises(HTTPException) as exc_info:
            svc.claim_next_request("prefect.transcode", "w3", None)

        assert exc_info.value.status_code == 500
        assert "Internal server error" in exc_info.value.detail

    @pytest.mark.unit
    def test_claim_next_request_reraises_http_exception(self, tr_repo, m_repo, svc) -> None:
        """claim_next_request re-raises HTTPExceptions unchanged."""

        tr_repo.claim_next.side_effect = HTTPException(status_code=418, detail="teapot")

        with pytest.raises(HTTPException) as exc_info:
            svc.claim_next_request("prefect.transcode", "w4", None)

        assert exc_info.value.status_code == 418
        assert exc_info.value.detail == "teapot"


class TestGetTransformRequestLogs:
    """Tests for TransformRequestService.get_transform_request_logs."""

    @pytest.mark.unit
    def test_get_logs_success(self, tr_repo, m_repo) -> None:
        """get_transform_request_logs returns the runner's logs as plain dicts."""

        runner = _RecordingRunner(
            logs=[
                LogEntry(
                    timestamp="2024-01-01T00:00:00",
                    level="INFO",
                    logger="prefect.flow",
                    message="working",
                    external_ref="job-123",
                )
            ]
        )
        svc = TransformRequestService(tr_repo, m_repo, runner)
        tr_repo.get.return_value = TransformRequestReadFactory(
            id=5, actioned=True, external_job_id="job-123"
        )

        result = svc.get_transform_request_logs(5)

        assert result == [
            {
                "timestamp": "2024-01-01T00:00:00",
                "level": "INFO",
                "logger": "prefect.flow",
                "message": "working",
                "external_ref": "job-123",
            }
        ]
        assert runner.fetched == ["job-123"]

    @pytest.mark.unit
    def test_get_logs_empty(self, tr_repo, m_repo) -> None:
        """get_transform_request_logs returns an empty list when runner has none."""

        runner = _RecordingRunner(logs=[])
        svc = TransformRequestService(tr_repo, m_repo, runner)
        tr_repo.get.return_value = TransformRequestReadFactory(
            id=5, actioned=True, external_job_id="job-123"
        )

        assert svc.get_transform_request_logs(5) == []

    @pytest.mark.unit
    def test_get_logs_not_found(self, tr_repo, m_repo, svc) -> None:
        """get_transform_request_logs raises 404 when the request is missing."""

        tr_repo.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.get_transform_request_logs(999)

        assert exc_info.value.status_code == 404
        assert "Transform request not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_get_logs_not_actioned(self, tr_repo, m_repo, svc) -> None:
        """get_transform_request_logs raises 409 when the request is not actioned."""

        tr_repo.get.return_value = TransformRequestReadFactory(
            id=5, actioned=False, external_job_id="job-123"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.get_transform_request_logs(5)

        assert exc_info.value.status_code == 409
        assert "not been actioned" in exc_info.value.detail

    @pytest.mark.unit
    def test_get_logs_missing_external_job_id(self, tr_repo, m_repo, svc) -> None:
        """get_transform_request_logs raises 409 when there is no external job id."""

        tr_repo.get.return_value = TransformRequestReadFactory(
            id=5, actioned=True, external_job_id=None
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.get_transform_request_logs(5)

        assert exc_info.value.status_code == 409
        assert "no external job id" in exc_info.value.detail


class TestDispatchOnCreate:
    """Tests for the best-effort dispatch triggered when a request is created."""

    @pytest.mark.unit
    def test_create_dispatches_job_to_runner(self, tr_repo, m_repo) -> None:
        """create_asset_transform_request dispatches the created job to the runner."""

        runner = _RecordingRunner()
        svc = TransformRequestService(tr_repo, m_repo, runner)
        created = TransformRequestReadFactory(
            id=1, asset_id=7, transform_type="prefect.transcode", parameters={"a": 1}
        )
        tr_repo.create.return_value = created

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")
        result = svc.create_asset_transform_request(7, payload)

        assert result is created
        assert len(runner.dispatched) == 1
        dispatch = runner.dispatched[0]
        assert dispatch.job_id == 1
        assert dispatch.job_type == "prefect.transcode"
        assert dispatch.parameters == {"a": 1}

    @pytest.mark.unit
    def test_create_dispatches_non_prefect_key_unchanged(self, tr_repo, m_repo) -> None:
        """A non-Prefect routing key is forwarded to the runner verbatim, untouched."""

        runner = _RecordingRunner()
        svc = TransformRequestService(tr_repo, m_repo, runner)
        created = TransformRequestReadFactory(
            id=1, asset_id=7, transform_type="webhook.thumbnail.generate", parameters={"a": 1}
        )
        tr_repo.create.return_value = created

        payload = TransformRequestCreatePublic(transform_type="webhook.thumbnail.generate")
        result = svc.create_asset_transform_request(7, payload)

        assert result is created
        assert len(runner.dispatched) == 1
        dispatch = runner.dispatched[0]
        assert dispatch.job_type == "webhook.thumbnail.generate"

    @pytest.mark.unit
    def test_create_does_not_dispatch_non_read_model(self, tr_repo, m_repo) -> None:
        """_dispatch is a no-op when the repository returns a non-read model."""

        runner = _RecordingRunner()
        svc = TransformRequestService(tr_repo, m_repo, runner)
        tr_repo.create.return_value = MagicMock()  # not a TransformRequestRead

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")
        svc.create_asset_transform_request(7, payload)

        assert runner.dispatched == []

    @pytest.mark.unit
    def test_create_swallows_dispatch_errors(self, tr_repo, m_repo) -> None:
        """A failing dispatch must not break request creation."""

        runner = _RecordingRunner(dispatch_error=RuntimeError("runner down"))
        svc = TransformRequestService(tr_repo, m_repo, runner)
        created = TransformRequestReadFactory(id=1, transform_type="prefect.transcode")
        tr_repo.create.return_value = created

        payload = TransformRequestCreatePublic(transform_type="prefect.transcode")
        result = svc.create_asset_transform_request(7, payload)

        assert result is created
        assert len(runner.dispatched) == 1
