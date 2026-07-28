# tests/unit/api/assets/test_streams.py
"""Unit tests for asset stream endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import StreamCreatePublic
from tests.factories import StreamReadFactory, get_stream_creation_json


class TestListAssetStreams:
    """Tests for GET /api/assets/{asset_id}/streams."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_streams_success(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/assets/{id}/streams returns list of streams."""
        expected_streams = [StreamReadFactory(asset_id=1) for _ in range(3)]
        stream_service_mock.get_asset_streams.return_value = expected_streams

        response = client.get("/api/assets/1/streams")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 3
        assert all(item["asset_id"] == 1 for item in response_data)
        stream_service_mock.get_asset_streams.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_streams_empty(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/assets/{id}/streams returns empty list when no streams."""
        stream_service_mock.get_asset_streams.return_value = []

        response = client.get("/api/assets/1/streams")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []
        stream_service_mock.get_asset_streams.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_streams_not_found(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/assets/{id}/streams returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        stream_service_mock.get_asset_streams.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999/streams")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateAssetStream:
    """Tests for POST /api/assets/{asset_id}/streams."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_stream_success(self, client: TestClient, stream_service_mock) -> None:
        """POST /api/assets/{id}/streams creates stream and returns 201."""
        expected_stream = StreamReadFactory(asset_id=1)
        stream_service_mock.create_stream.return_value = expected_stream

        payload = get_stream_creation_json(expected_stream)
        response = client.post("/api/assets/1/streams", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_stream.id
        assert response_data["asset_id"] == 1

        # Verify service called with correct parameters
        stream_service_mock.create_stream.assert_called_once()
        call_args = stream_service_mock.create_stream.call_args[0]
        assert call_args[0] == 1  # asset_id
        assert isinstance(call_args[1], StreamCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_stream_validation_error(self, client: TestClient) -> None:
        """POST /api/assets/{id}/streams returns 422 for invalid payload."""
        invalid_payload = {
            "codec": "h264",
            # Missing required fields
        }

        response = client.post("/api/assets/1/streams", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_stream_asset_not_found(
        self, client: TestClient, stream_service_mock
    ) -> None:
        """POST /api/assets/{id}/streams returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        stream_service_mock.create_stream.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        stream = StreamReadFactory()
        payload = get_stream_creation_json(stream)
        response = client.post("/api/assets/999/streams", json=payload)

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestDeleteAssetStreams:
    """Tests for DELETE /api/assets/{asset_id}/streams."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_asset_streams_success(self, client: TestClient, stream_service_mock) -> None:
        """DELETE /api/assets/{id}/streams deletes all streams and returns 204."""
        stream_service_mock.delete_asset_streams.return_value = None

        response = client.delete("/api/assets/1/streams")

        assert response.status_code == HTTPStatus.NO_CONTENT
        stream_service_mock.delete_asset_streams.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_asset_streams_not_found(self, client: TestClient, stream_service_mock) -> None:
        """DELETE /api/assets/{id}/streams returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        stream_service_mock.delete_asset_streams.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.delete("/api/assets/999/streams")

        assert response.status_code == HTTPStatus.NOT_FOUND
