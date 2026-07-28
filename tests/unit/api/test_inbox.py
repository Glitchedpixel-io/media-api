# tests/unit/api/test_inbox.py
"""Unit tests for inbox router endpoints."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import InboxImportRequest
from tests.factories import AssetReadFactory, InboxItemFactory


class TestListInbox:
    """Tests for GET /api/inbox."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_inbox_success(self, client: TestClient, inbox_service_mock: Mock) -> None:
        """GET /api/inbox returns list of inbox items."""
        expected_item = InboxItemFactory()
        inbox_service_mock.list_inbox.return_value = [expected_item]

        response = client.get("/api/inbox")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 1
        assert response_data[0]["path"] == expected_item.path
        assert response_data[0]["type"] == expected_item.type.value
        inbox_service_mock.list_inbox.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_inbox_empty(self, client: TestClient, inbox_service_mock: Mock) -> None:
        """GET /api/inbox returns empty list when no items exist."""
        inbox_service_mock.list_inbox.return_value = []

        response = client.get("/api/inbox")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []


class TestImportInboxFile:
    """Tests for POST /api/inbox."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_import_file_success(self, client: TestClient, inbox_service_mock: Mock) -> None:
        """POST /api/inbox returns 201 and imports file."""
        expected_asset = AssetReadFactory(
            path="movies/Movie Title (1999)/Movie Title (1999).mp4",
            filename="Movie Title (1999).mp4",
        )
        inbox_service_mock.import_file.return_value = expected_asset

        payload = {
            "source": "foldera/folder_b/filec.mmp4",
            "target": "movies/Movie Title (1999)/Movie Title (1999).mp4",
        }
        response = client.post("/api/inbox", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["path"] == expected_asset.path
        assert response_data["filename"] == expected_asset.filename
        inbox_service_mock.import_file.assert_called_once_with(
            InboxImportRequest(
                source="foldera/folder_b/filec.mmp4",
                target="movies/Movie Title (1999)/Movie Title (1999).mp4",
            )
        )

    @pytest.mark.unit
    @pytest.mark.api
    def test_import_file_missing_required_field(
        self, client: TestClient, inbox_service_mock: Mock
    ) -> None:
        """POST /api/inbox returns 422 when required field missing."""
        invalid_payload = {
            "from": "foldera/folder_b/filec.mmp4",  # Wrong field name
            "target": "movies/Movie Title (1999)/Movie Title (1999).mp4",
        }

        response = client.post("/api/inbox", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        inbox_service_mock.import_file.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.api
    def test_import_file_invalid_field(self, client: TestClient, inbox_service_mock: Mock) -> None:
        """POST /api/inbox returns 422 for invalid field."""
        invalid_payload = {
            "source": "foldera/folder_b/filec.mmp4",
            "target": "movies/Movie Title (1999)/Movie Title (1999).mp4",
            "secret": "secretsauce",  # Extra field not allowed
        }

        response = client.post("/api/inbox", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        inbox_service_mock.import_file.assert_not_called()


class TestDeleteInboxFile:
    """Tests for DELETE /api/inbox."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_file_success(self, client: TestClient, inbox_service_mock: Mock) -> None:
        """DELETE /api/inbox returns 204 and deletes file."""
        inbox_service_mock.delete.return_value = None

        params = {"source": "foldera/folder_b/filec.mmp4"}
        response = client.delete("/api/inbox", params=params)

        assert response.status_code == HTTPStatus.NO_CONTENT
        inbox_service_mock.delete.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_file_missing_required_param(
        self, client: TestClient, inbox_service_mock: Mock
    ) -> None:
        """DELETE /api/inbox returns 422 when required param missing."""
        invalid_params = {"file": "foldera/folder_b/filec.mmp4"}  # Wrong param name

        response = client.delete("/api/inbox", params=invalid_params)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        inbox_service_mock.delete.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_file_no_params(self, client: TestClient, inbox_service_mock: Mock) -> None:
        """DELETE /api/inbox returns 422 when no params provided."""
        response = client.delete("/api/inbox")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        inbox_service_mock.delete.assert_not_called()
