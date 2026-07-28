# tests/unit/api/test_id_schemes.py
"""Unit tests for ID schemes router endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import IdSchemeCreatePublic, IdSchemePatchPublic, IdSchemeRead


class TestListIdSchemes:
    """Tests for GET /api/id_schemes."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_schemes_success(self, client: TestClient, id_scheme_service_mock) -> None:
        """GET /api/id_schemes returns list of schemes."""
        expected_schemes = [
            IdSchemeRead(id=1, code="imdb", label="IMDb", validator=None),
            IdSchemeRead(id=2, code="tmdb", label="TMDB", validator=None),
        ]
        id_scheme_service_mock.get_schemes.return_value = expected_schemes

        response = client.get("/api/id_schemes")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data) == 2
        id_scheme_service_mock.get_schemes.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_schemes_empty(self, client: TestClient, id_scheme_service_mock) -> None:
        """GET /api/id_schemes returns empty list when no schemes exist."""
        id_scheme_service_mock.get_schemes.return_value = []

        response = client.get("/api/id_schemes")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []


class TestGetIdScheme:
    """Tests for GET /api/id_schemes/{scheme_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_scheme_success(self, client: TestClient, id_scheme_service_mock) -> None:
        """GET /api/id_schemes/{id} returns the scheme."""
        expected_scheme = IdSchemeRead(id=42, code="test", label="Test Scheme", validator=None)
        id_scheme_service_mock.get_scheme.return_value = expected_scheme

        response = client.get("/api/id_schemes/42")

        assert response.status_code == HTTPStatus.OK
        assert response.json()["id"] == 42
        assert response.json()["code"] == "test"
        id_scheme_service_mock.get_scheme.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_scheme_not_found(self, client: TestClient, id_scheme_service_mock) -> None:
        """GET /api/id_schemes/{id} returns 404 when scheme not found."""
        from fastapi import HTTPException

        id_scheme_service_mock.get_scheme.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Scheme not found"
        )

        response = client.get("/api/id_schemes/999")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateIdScheme:
    """Tests for POST /api/id_schemes."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_scheme_success(self, client: TestClient, id_scheme_service_mock) -> None:
        """POST /api/id_schemes returns 201 and created scheme."""
        expected_scheme = IdSchemeRead(id=3, code="yt", label="YouTube", validator=None)
        id_scheme_service_mock.create_scheme.return_value = expected_scheme

        payload = IdSchemeCreatePublic(code="yt", label="YouTube", validator=None)
        response = client.post("/api/id_schemes", json=payload.model_dump())

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == 3
        assert response_data["code"] == "yt"

        # Verify service called with correct schema type
        id_scheme_service_mock.create_scheme.assert_called_once()
        call_arg = id_scheme_service_mock.create_scheme.call_args[0][0]
        assert isinstance(call_arg, IdSchemeCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_scheme_missing_required_field(self, client: TestClient) -> None:
        """POST /api/id_schemes returns 422 when required field missing."""
        invalid_payload = {"code": "test"}  # Missing label

        response = client.post("/api/id_schemes", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_scheme_invalid_field(self, client: TestClient) -> None:
        """POST /api/id_schemes returns 422 for invalid field."""
        invalid_payload = {"code": "test", "label": "Test", "nonexistent_field": "value"}

        response = client.post("/api/id_schemes", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestUpdateIdScheme:
    """Tests for PATCH /api/id_schemes/{scheme_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_scheme_success(self, client: TestClient, id_scheme_service_mock) -> None:
        """PATCH /api/id_schemes/{id} updates the scheme."""
        updated_scheme = IdSchemeRead(id=5, code="tvdb", label="TVDB Updated", validator=None)
        id_scheme_service_mock.update_scheme.return_value = updated_scheme

        response = client.patch("/api/id_schemes/5", json={"label": "TVDB Updated"})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["label"] == "TVDB Updated"

        # Verify service called with exclude_none=True
        id_scheme_service_mock.update_scheme.assert_called_once()
        call_args = id_scheme_service_mock.update_scheme.call_args[0]
        assert call_args[0] == 5
        assert isinstance(call_args[1], IdSchemePatchPublic)
        assert call_args[2] is True  # exclude_none

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_scheme_not_found(self, client: TestClient, id_scheme_service_mock) -> None:
        """PATCH /api/id_schemes/{id} returns 404 when scheme not found."""
        from fastapi import HTTPException

        id_scheme_service_mock.update_scheme.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Scheme not found"
        )

        response = client.patch("/api/id_schemes/999", json={"label": "New Label"})

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_scheme_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/id_schemes/{id} returns 422 for invalid field."""
        response = client.patch("/api/id_schemes/1", json={"nonexistent_field": "value"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
