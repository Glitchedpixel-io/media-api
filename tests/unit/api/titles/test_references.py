# tests/unit/api/titles/test_references.py
"""Unit tests for title reference endpoints."""

from __future__ import annotations

import json

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import (
    TitleReferenceCreatePublic,
    TitleReferencePatchPublic,
)
from tests.factories import TitleReferenceReadFactory, get_title_reference_creation_json


class TestListTitleReferences:
    """Tests for GET /api/titles/{title_id}/references."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_references_success(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """GET /api/titles/{id}/references returns list of references."""
        expected_refs = [TitleReferenceReadFactory(title_id=42) for _ in range(3)]
        title_reference_service_mock.get_title_references.return_value = expected_refs

        response = client.get("/api/titles/42/references")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data) == 3
        assert all(ref["title_id"] == 42 for ref in response_data)
        title_reference_service_mock.get_title_references.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_references_empty(self, client: TestClient, title_reference_service_mock) -> None:
        """GET /api/titles/{id}/references returns empty list when no references."""
        title_reference_service_mock.get_title_references.return_value = []

        response = client.get("/api/titles/5/references")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_references_title_not_found(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """GET /api/titles/{id}/references returns 404 when title not found."""
        from fastapi import HTTPException

        title_reference_service_mock.get_title_references.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.get("/api/titles/999/references")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateTitleReference:
    """Tests for POST /api/titles/{title_id}/references."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_reference_success(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """POST /api/titles/{id}/references returns 201 and created reference."""
        expected_ref = TitleReferenceReadFactory(title_id=10)
        title_reference_service_mock.create_reference.return_value = expected_ref

        payload = get_title_reference_creation_json(expected_ref)
        response = client.post("/api/titles/10/references", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_ref.id
        assert response_data["title_id"] == 10
        assert response_data["reference_type"] == expected_ref.reference_type
        assert response_data["reference_url"] == expected_ref.reference_url

        # Verify service called with correct parameters
        title_reference_service_mock.create_reference.assert_called_once()
        call_args = title_reference_service_mock.create_reference.call_args[0]
        assert call_args[0] == 10  # title_id
        assert isinstance(call_args[1], TitleReferenceCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_reference_missing_required_field(self, client: TestClient) -> None:
        """POST /api/titles/{id}/references returns 422 when required field missing."""
        invalid_payload = {
            "reference_type": "article"
            # Missing: reference_url
        }

        response = client.post("/api/titles/5/references", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_reference_invalid_type(self, client: TestClient) -> None:
        """POST /api/titles/{id}/references returns 422 for invalid reference_type."""
        invalid_payload = {
            "reference_type": "invalid_type",
            "reference_url": "https://example.com",
        }

        response = client.post("/api/titles/1/references", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_reference_title_not_found(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """POST /api/titles/{id}/references returns 404 when title not found."""
        from fastapi import HTTPException

        title_reference_service_mock.create_reference.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        payload = {"reference_type": "article", "reference_url": "https://example.com"}
        response = client.post("/api/titles/999/references", json=payload)

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestUpdateTitleReference:
    """Tests for PATCH /api/titles/{title_id}/references/{reference_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_reference_success(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """PATCH /api/titles/{id}/references/{ref_id} returns 200 and updated reference."""
        updated_ref = TitleReferenceReadFactory(
            id=5, title_id=10, reference_url="https://updated.com"
        )
        title_reference_service_mock.update_title_reference.return_value = updated_ref

        response = client.patch(
            "/api/titles/10/references/5",
            json={"reference_url": "https://updated.com"},
        )

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 5
        assert response_data["reference_url"] == "https://updated.com"

        # Verify service called with correct parameters
        title_reference_service_mock.update_title_reference.assert_called_once()
        call_kwargs = title_reference_service_mock.update_title_reference.call_args[1]
        assert call_kwargs["title_id"] == 10
        assert call_kwargs["title_reference_id"] == 5
        assert isinstance(call_kwargs["update"], TitleReferencePatchPublic)
        assert call_kwargs["exclude_none"] is True

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_reference_partial_fields(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """PATCH /api/titles/{id}/references/{ref_id} allows partial updates."""
        updated_ref = TitleReferenceReadFactory()
        title_reference_service_mock.update_title_reference.return_value = updated_ref

        response = client.patch(
            "/api/titles/1/references/2",
            json={"reference_url": "https://new-url.com"},
        )

        assert response.status_code == HTTPStatus.OK
        title_reference_service_mock.update_title_reference.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_reference_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/titles/{id}/references/{ref_id} returns 422 for invalid field."""
        response = client.patch(
            "/api/titles/1/references/2",
            json={"nonexistent_field": "value"},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_reference_not_found(
        self, client: TestClient, title_reference_service_mock
    ) -> None:
        """PATCH /api/titles/{id}/references/{ref_id} returns 404 when not found."""
        from fastapi import HTTPException

        title_reference_service_mock.update_title_reference.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Reference not found"
        )

        response = client.patch(
            "/api/titles/1/references/999",
            json={"reference_url": "https://example.com"},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND
