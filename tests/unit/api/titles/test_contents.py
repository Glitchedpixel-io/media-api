# tests/unit/api/titles/test_contents.py
"""Unit tests for title content endpoints."""

from __future__ import annotations

import json

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import (
    TitleContentInsert,
    TitleContentPatchPublic,
)
from tests.factories import TitleContentReadFactory, get_title_content_creation_json


class TestListTitleContents:
    """Tests for GET /api/titles/{parent_title_id}/contents."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_contents_success(self, client: TestClient, title_content_service_mock) -> None:
        """GET /api/titles/{id}/contents returns list of contents."""
        expected_contents = [TitleContentReadFactory(parent_title_id=42) for _ in range(3)]
        title_content_service_mock.get_title_content.return_value = expected_contents

        response = client.get("/api/titles/42/contents")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data) == 3
        assert all(content["parent_title_id"] == 42 for content in response_data)
        title_content_service_mock.get_title_content.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_contents_empty(self, client: TestClient, title_content_service_mock) -> None:
        """GET /api/titles/{id}/contents returns empty list when no contents."""
        title_content_service_mock.get_title_content.return_value = []

        response = client.get("/api/titles/5/contents")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_contents_title_not_found(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """GET /api/titles/{id}/contents returns 404 when title not found."""
        from fastapi import HTTPException

        title_content_service_mock.get_title_content.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.get("/api/titles/999/contents")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateTitleContent:
    """Tests for POST /api/titles/{parent_title_id}/contents."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_content_success(self, client: TestClient, title_content_service_mock) -> None:
        """POST /api/titles/{id}/contents returns 201 and created content."""
        expected_content = TitleContentReadFactory(parent_title_id=10)
        title_content_service_mock.insert_positioned.return_value = expected_content

        payload = get_title_content_creation_json(expected_content)
        response = client.post("/api/titles/10/contents", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_content.id
        assert response_data["parent_title_id"] == 10

        # Verify service called with correct parameters
        title_content_service_mock.insert_positioned.assert_called_once()
        call_args = title_content_service_mock.insert_positioned.call_args[0]
        assert call_args[0] == 10  # parent_title_id
        assert isinstance(call_args[1], TitleContentInsert)
        # Check anchor kwarg
        call_kwargs = title_content_service_mock.insert_positioned.call_args[1]
        assert call_kwargs["anchor"] == "end"

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_content_invalid_field(self, client: TestClient) -> None:
        """POST /api/titles/{id}/contents returns 422 for invalid field."""
        invalid_payload = {"kind": "asset", "asset_id": 123, "nonexistent_field": "value"}

        response = client.post("/api/titles/5/contents", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_content_title_not_found(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """POST /api/titles/{id}/contents returns 404 when title not found."""
        from fastapi import HTTPException

        title_content_service_mock.insert_positioned.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        payload = {"kind": "asset", "asset_id": 123}
        response = client.post("/api/titles/999/contents", json=payload)

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestUpdateTitleContent:
    """Tests for PATCH /api/titles/{parent_title_id}/contents/{title_contents_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_content_success(self, client: TestClient, title_content_service_mock) -> None:
        """PATCH /api/titles/{id}/contents/{content_id} returns 200 and updated content."""
        updated_content = TitleContentReadFactory(id=5, parent_title_id=10, label="Updated Label")
        title_content_service_mock.update_title_content.return_value = updated_content

        response = client.patch(
            "/api/titles/10/contents/5",
            json={"label": "Updated Label"},
        )

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 5
        assert response_data["label"] == "Updated Label"

        # Verify service called with correct parameters
        title_content_service_mock.update_title_content.assert_called_once()
        call_kwargs = title_content_service_mock.update_title_content.call_args[1]
        assert call_kwargs["parent_title_id"] == 10
        assert call_kwargs["title_contents_id"] == 5
        assert isinstance(call_kwargs["update"], TitleContentPatchPublic)
        assert call_kwargs["exclude_none"] is True

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_content_partial_fields(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """PATCH /api/titles/{id}/contents/{content_id} allows partial updates."""
        updated_content = TitleContentReadFactory()
        title_content_service_mock.update_title_content.return_value = updated_content

        response = client.patch(
            "/api/titles/1/contents/2",
            json={"label": "New Label"},
        )

        assert response.status_code == HTTPStatus.OK
        title_content_service_mock.update_title_content.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_content_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/titles/{id}/contents/{content_id} returns 422 for invalid field."""
        response = client.patch(
            "/api/titles/1/contents/2",
            json={"nonexistent_field": "value"},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_content_not_found(self, client: TestClient, title_content_service_mock) -> None:
        """PATCH /api/titles/{id}/contents/{content_id} returns 404 when not found."""
        from fastapi import HTTPException

        title_content_service_mock.update_title_content.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Content not found"
        )

        response = client.patch(
            "/api/titles/1/contents/999",
            json={"label": "New Label"},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestDeleteTitleContent:
    """Tests for DELETE /api/titles/{parent_title_id}/contents/{title_contents_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_content_success(self, client: TestClient, title_content_service_mock) -> None:
        """DELETE /api/titles/{id}/contents/{content_id} returns 204 on success."""
        title_content_service_mock.unlink_content.return_value = None

        response = client.delete("/api/titles/10/contents/5")

        assert response.status_code == HTTPStatus.NO_CONTENT
        title_content_service_mock.unlink_content.assert_called_once_with(10, 5)

    @pytest.mark.unit
    @pytest.mark.api
    def test_delete_content_not_found(self, client: TestClient, title_content_service_mock) -> None:
        """DELETE /api/titles/{id}/contents/{content_id} returns 404 when not found."""
        from fastapi import HTTPException

        title_content_service_mock.unlink_content.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Content not found"
        )

        response = client.delete("/api/titles/10/contents/999")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateTitleContentPositioned:
    """Tests for POST /api/titles/{parent_title_id}/contents/positioned."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_positioned_at_start(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """POST /api/titles/{id}/contents/positioned?position=start inserts at start."""
        expected_content = TitleContentReadFactory(parent_title_id=10)
        title_content_service_mock.insert_positioned.return_value = expected_content

        payload = get_title_content_creation_json(expected_content)
        response = client.post(
            "/api/titles/10/contents/positioned?position=start",
            json=payload,
        )

        assert response.status_code == HTTPStatus.CREATED
        call_kwargs = title_content_service_mock.insert_positioned.call_args[1]
        assert call_kwargs["anchor"] == "start"

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_positioned_before_id(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """POST /api/titles/{id}/contents/positioned?before_id=N inserts before N."""
        expected_content = TitleContentReadFactory(parent_title_id=10)
        title_content_service_mock.insert_positioned.return_value = expected_content

        payload = get_title_content_creation_json(expected_content)
        response = client.post(
            "/api/titles/10/contents/positioned?before_id=3",
            json=payload,
        )

        assert response.status_code == HTTPStatus.CREATED
        call_kwargs = title_content_service_mock.insert_positioned.call_args[1]
        assert call_kwargs["before_id"] == 3

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_positioned_after_id(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """POST /api/titles/{id}/contents/positioned?after_id=N inserts after N."""
        expected_content = TitleContentReadFactory(parent_title_id=10)
        title_content_service_mock.insert_positioned.return_value = expected_content

        payload = get_title_content_creation_json(expected_content)
        response = client.post(
            "/api/titles/10/contents/positioned?after_id=7",
            json=payload,
        )

        assert response.status_code == HTTPStatus.CREATED
        call_kwargs = title_content_service_mock.insert_positioned.call_args[1]
        assert call_kwargs["after_id"] == 7


class TestReorderTitleContent:
    """Tests for PATCH /api/titles/{parent_title_id}/contents/{title_contents_id}/reorder."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_reorder_to_start(self, client: TestClient, title_content_service_mock) -> None:
        """PATCH /api/titles/{id}/contents/{content_id}/reorder?position=start moves to start."""
        reordered_content = TitleContentReadFactory(id=5, parent_title_id=10)
        title_content_service_mock.reorder_content.return_value = reordered_content

        response = client.patch("/api/titles/10/contents/5/reorder?position=start")

        assert response.status_code == HTTPStatus.OK
        title_content_service_mock.reorder_content.assert_called_once()
        call_kwargs = title_content_service_mock.reorder_content.call_args[1]
        assert call_kwargs["anchor"] == "start"

    @pytest.mark.unit
    @pytest.mark.api
    def test_reorder_before_id(self, client: TestClient, title_content_service_mock) -> None:
        """PATCH /api/titles/{id}/contents/{content_id}/reorder?before_id=N moves before N."""
        reordered_content = TitleContentReadFactory(id=5, parent_title_id=10)
        title_content_service_mock.reorder_content.return_value = reordered_content

        response = client.patch("/api/titles/10/contents/5/reorder?before_id=3")

        assert response.status_code == HTTPStatus.OK
        call_kwargs = title_content_service_mock.reorder_content.call_args[1]
        assert call_kwargs["before_id"] == 3

    @pytest.mark.unit
    @pytest.mark.api
    def test_reorder_after_id(self, client: TestClient, title_content_service_mock) -> None:
        """PATCH /api/titles/{id}/contents/{content_id}/reorder?after_id=N moves after N."""
        reordered_content = TitleContentReadFactory(id=5, parent_title_id=10)
        title_content_service_mock.reorder_content.return_value = reordered_content

        response = client.patch("/api/titles/10/contents/5/reorder?after_id=7")

        assert response.status_code == HTTPStatus.OK
        call_kwargs = title_content_service_mock.reorder_content.call_args[1]
        assert call_kwargs["after_id"] == 7

    @pytest.mark.unit
    @pytest.mark.api
    def test_reorder_not_found(self, client: TestClient, title_content_service_mock) -> None:
        """PATCH /api/titles/{id}/contents/{content_id}/reorder returns 404 when not found."""
        from fastapi import HTTPException

        title_content_service_mock.reorder_content.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Content not found"
        )

        response = client.patch("/api/titles/10/contents/999/reorder?position=start")

        assert response.status_code == HTTPStatus.NOT_FOUND
