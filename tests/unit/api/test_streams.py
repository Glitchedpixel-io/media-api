# tests/unit/api/test_streams.py
"""Unit tests for streams router endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import PageInfo, PaginatedResponse, StreamPatchPublic, StreamRead
from tests.factories import StreamReadFactory


class TestListStreams:
    """Tests for GET /api/streams."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_streams_success(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/streams returns a page of streams."""
        expected_streams = [StreamReadFactory() for _ in range(3)]
        stream_service_mock.get_streams.return_value = PaginatedResponse[StreamRead](
            items=expected_streams, page=PageInfo(next="cursor", prev=None)
        )

        response = client.get("/api/streams")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 3
        assert response_data["page"]["next"] == "cursor"
        stream_service_mock.get_streams.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_streams_empty(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/streams returns an empty page when no streams exist."""
        stream_service_mock.get_streams.return_value = PaginatedResponse[StreamRead](
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/streams")

        assert response.status_code == HTTPStatus.OK
        assert response.json()["items"] == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_streams_rejects_limit_above_cap(self, client: TestClient) -> None:
        """The page size cap is enforced by validation, not left to the caller."""
        response = client.get("/api/streams?limit=501")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_streams_forwards_params(self, client: TestClient, stream_service_mock) -> None:
        """Query params reach the service rather than being silently dropped."""
        stream_service_mock.get_streams.return_value = PaginatedResponse[StreamRead](
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/streams?asset_id=42&limit=5")

        assert response.status_code == HTTPStatus.OK
        params = stream_service_mock.get_streams.call_args.args[0]
        assert params.asset_id == 42
        assert params.limit == 5


class TestGetStream:
    """Tests for GET /api/streams/{stream_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_stream_success(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/streams/{id} returns the stream."""
        expected_stream = StreamReadFactory(id=42)
        stream_service_mock.get_stream.return_value = expected_stream

        response = client.get("/api/streams/42")

        assert response.status_code == HTTPStatus.OK
        assert response.json()["id"] == 42
        stream_service_mock.get_stream.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_stream_not_found(self, client: TestClient, stream_service_mock) -> None:
        """GET /api/streams/{id} returns 404 when stream not found."""
        from fastapi import HTTPException

        stream_service_mock.get_stream.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Stream not found"
        )

        response = client.get("/api/streams/999")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestUpdateStream:
    """Tests for PATCH /api/streams/{stream_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_stream_success(self, client: TestClient, stream_service_mock) -> None:
        """PATCH /api/streams/{id} updates the stream."""
        updated_stream = StreamReadFactory(id=5)
        stream_service_mock.update_stream.return_value = updated_stream

        response = client.patch("/api/streams/5", json={"language": "eng"})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["id"] == 5

        # Verify service called with exclude_none=True
        stream_service_mock.update_stream.assert_called_once()
        call_args = stream_service_mock.update_stream.call_args[0]
        assert call_args[0] == 5
        assert isinstance(call_args[1], StreamPatchPublic)
        assert call_args[2] is True  # exclude_none

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_stream_not_found(self, client: TestClient, stream_service_mock) -> None:
        """PATCH /api/streams/{id} returns 404 when stream not found."""
        from fastapi import HTTPException

        stream_service_mock.update_stream.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Stream not found"
        )

        response = client.patch("/api/streams/999", json={"language": "eng"})

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_stream_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/streams/{id} returns 422 for invalid field."""
        response = client.patch("/api/streams/1", json={"nonexistent_field": "value"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
