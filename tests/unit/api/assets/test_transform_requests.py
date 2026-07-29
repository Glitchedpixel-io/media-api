# tests/unit/api/assets/test_transform_requests.py
"""Unit tests for asset transform request endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import TransformRequestCreatePublic
from tests.factories import TransformRequestReadFactory, get_transform_request_creation_json


class TestListAssetTransformRequests:
    """Tests for GET /api/assets/{asset_id}/transform_requests."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_transform_requests_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """GET /api/assets/{id}/transform_requests returns list of requests."""
        expected_requests = [TransformRequestReadFactory(asset_id=1) for _ in range(2)]
        transform_request_service_mock.get_asset_transform_requests.return_value = expected_requests

        response = client.get("/api/assets/1/transform_requests")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 2
        assert all(item["asset_id"] == 1 for item in response_data)
        transform_request_service_mock.get_asset_transform_requests.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_transform_requests_empty(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """GET /api/assets/{id}/transform_requests returns empty list when none exist."""
        transform_request_service_mock.get_asset_transform_requests.return_value = []

        response = client.get("/api/assets/1/transform_requests")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []
        transform_request_service_mock.get_asset_transform_requests.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_transform_requests_asset_not_found(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """GET /api/assets/{id}/transform_requests returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        transform_request_service_mock.get_asset_transform_requests.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999/transform_requests")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateAssetTransformRequest:
    """Tests for POST /api/assets/{asset_id}/transform_requests."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_transform_request_success(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """POST /api/assets/{id}/transform_requests creates request and returns 201."""
        expected_request = TransformRequestReadFactory(asset_id=1)
        transform_request_service_mock.create_asset_transform_request.return_value = (
            expected_request
        )

        payload = get_transform_request_creation_json(expected_request)
        response = client.post("/api/assets/1/transform_requests", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_request.id
        assert response_data["asset_id"] == 1

        # Verify service called with correct parameters
        transform_request_service_mock.create_asset_transform_request.assert_called_once()
        call_args = transform_request_service_mock.create_asset_transform_request.call_args[0]
        assert call_args[0] == 1  # asset_id
        assert isinstance(call_args[1], TransformRequestCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_transform_request_validation_error(self, client: TestClient) -> None:
        """POST /api/assets/{id}/transform_requests returns 422 for invalid payload."""
        invalid_payload = {
            "unknown_field": "value"
            # Missing required fields
        }

        response = client.post("/api/assets/1/transform_requests", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_transform_request_empty_local_type_is_422(
        self, client: TestClient
    ) -> None:
        """`prefect.` (empty provider-local type) fails the routing-key shape check."""
        response = client.post(
            "/api/assets/1/transform_requests", json={"transform_type": "prefect."}
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_transform_request_asset_not_found(
        self, client: TestClient, transform_request_service_mock
    ) -> None:
        """POST /api/assets/{id}/transform_requests returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        transform_request_service_mock.create_asset_transform_request.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        transform_request = TransformRequestReadFactory()
        payload = get_transform_request_creation_json(transform_request)
        response = client.post("/api/assets/999/transform_requests", json=payload)

        assert response.status_code == HTTPStatus.NOT_FOUND
