# tests/unit/api/test_transform_requests.py
"""Unit tests for transform requests router endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import (
    PageInfo,
    PaginatedResponse,
    TransformRequestClaim,
    TransformRequestCreatePublic,
    TransformRequestListParams,
    TransformRequestPatchPublic,
    TransformRequestReadExpanded,
)
from tests.factories import (
    TransformRequestReadExpandedFactory,
    TransformRequestReadFactory,
    get_transform_request_creation_json,
)


class TestListTransformRequests:
    """Tests for GET /api/transform_requests."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_requests_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """GET /api/transform_requests returns a paginated list."""
        expected_requests = [TransformRequestReadExpandedFactory() for _ in range(3)]
        transform_request_service_mock.get_transform_requests.return_value = PaginatedResponse[
            TransformRequestReadExpanded
        ](items=expected_requests, page=PageInfo(next=None, prev=None))

        response = client.get("/api/transform_requests")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 3
        assert response_data["page"]["next"] is None

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_requests_with_filters(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """GET /api/transform_requests supports filtering."""
        expected_requests = [TransformRequestReadExpandedFactory()]
        transform_request_service_mock.get_transform_requests.return_value = PaginatedResponse[
            TransformRequestReadExpanded
        ](items=expected_requests, page=PageInfo(next=None, prev=None))

        response = client.get("/api/transform_requests?actioned=true&transform_type=test")

        assert response.status_code == HTTPStatus.OK
        transform_request_service_mock.get_transform_requests.assert_called_once()


class TestGetTransformRequest:
    """Tests for GET /api/transform_requests/{request_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_request_success(self, client: TestClient, transform_request_service_mock) -> None:
        """GET /api/transform_requests/{id} returns the request."""
        expected_request = TransformRequestReadFactory(id=42)
        transform_request_service_mock.get_transform_request.return_value = expected_request

        response = client.get("/api/transform_requests/42")

        assert response.status_code == HTTPStatus.OK
        assert response.json()["id"] == 42
        transform_request_service_mock.get_transform_request.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_request_not_found(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """GET /api/transform_requests/{id} returns 404 when not found."""
        from fastapi import HTTPException

        transform_request_service_mock.get_transform_request.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Request not found"
        )

        response = client.get("/api/transform_requests/999")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestGetTransformRequestLogs:
    """Tests for GET /api/transform_requests/{request_id}/logs."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_logs_success(self, client: TestClient, transform_request_service_mock) -> None:
        """GET /api/transform_requests/{id}/logs returns logs."""
        expected_logs = [
            {
                "timestamp": "2024-01-01T00:00:00",
                "level": "INFO",
                "logger": "prefect.flow",
                "message": "Processing",
                "external_ref": "job-123",
            }
        ]
        transform_request_service_mock.get_transform_request_logs.return_value = expected_logs

        response = client.get("/api/transform_requests/42/logs")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == expected_logs


class TestUpdateTransformRequest:
    """Tests for PATCH /api/transform_requests/{request_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_request_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """PATCH /api/transform_requests/{id} updates the request."""
        updated_request = TransformRequestReadFactory(id=5)
        transform_request_service_mock.update_transform_request.return_value = updated_request

        response = client.patch("/api/transform_requests/5", json={"worker_notes": "Updated notes"})

        assert response.status_code == HTTPStatus.OK
        transform_request_service_mock.update_transform_request.assert_called_once()
        call_args = transform_request_service_mock.update_transform_request.call_args[0]
        assert call_args[0] == 5
        assert isinstance(call_args[1], TransformRequestPatchPublic)


class TestRetryTransformRequest:
    """Tests for PATCH /api/transform_requests/{request_id}/retry."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_retry_request_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """PATCH /api/transform_requests/{id}/retry retries the request."""
        retried_request = TransformRequestReadFactory(id=5)
        transform_request_service_mock.retry_transform_request.return_value = retried_request

        response = client.patch("/api/transform_requests/5/retry")

        assert response.status_code == HTTPStatus.OK
        transform_request_service_mock.retry_transform_request.assert_called_once_with(5)


class TestHeartbeat:
    """Tests for PATCH /api/transform_requests/{request_id}/heartbeat."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_heartbeat_success(self, client: TestClient, transform_request_service_mock) -> None:
        """PATCH /api/transform_requests/{id}/heartbeat marks heartbeat."""
        transform_request_service_mock.mark_heartbeat.return_value = None

        response = client.patch("/api/transform_requests/5/heartbeat")

        assert response.status_code == HTTPStatus.OK
        transform_request_service_mock.mark_heartbeat.assert_called_once_with(5)


class TestClaimRequest:
    """Tests for POST /api/transform_requests/claim."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_claim_request_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """POST /api/transform_requests/claim claims next available request."""
        expected_request = TransformRequestReadExpandedFactory()
        transform_request_service_mock.claim_next_request.return_value = expected_request

        response = client.post(
            "/api/transform_requests/claim",
            json={
                "transform_type": "test",
                "worker": "test-worker",
                "external_job_id": "job-123",
            },
        )

        assert response.status_code == HTTPStatus.OK
        transform_request_service_mock.claim_next_request.assert_called_once()


class TestCreateLinkedRequest:
    """Tests for POST /api/transform_requests/{request_id}/link."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_linked_request_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """POST /api/transform_requests/{id}/link creates linked request."""
        linked_request = TransformRequestReadFactory()
        transform_request_service_mock.create_linked_request.return_value = linked_request

        response = client.post(
            "/api/transform_requests/5/link",
            json={"transform_type": "test", "parameters": {}},
        )

        assert response.status_code == HTTPStatus.CREATED
        transform_request_service_mock.create_linked_request.assert_called_once()
