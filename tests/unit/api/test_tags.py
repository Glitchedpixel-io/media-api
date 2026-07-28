# tests/unit/api/test_tags.py
"""Unit tests for top-level tags router endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import (
    PageInfo,
    PaginatedResponse,
    TagCreatePublic,
    TagListParams,
    TagPatchPublic,
    TagRead,
)
from tests.factories import TagReadFactory, get_tag_creation_json


class TestListTags:
    """Tests for GET /api/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_tags_success(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags returns paginated list of top-level tags."""
        expected_tags = [TagReadFactory() for _ in range(3)]
        tag_service_mock.get_tags.return_value = PaginatedResponse[TagRead](
            items=expected_tags, page=PageInfo(next="abc", prev="def")
        )

        response = client.get("/api/tags")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 3
        assert response_data["page"]["next"] == "abc"

        # Verify service called with parent_id=None for top-level tags
        tag_service_mock.get_tags.assert_called_once()
        call_kwargs = tag_service_mock.get_tags.call_args[1]
        assert isinstance(call_kwargs["params"], TagListParams)
        assert call_kwargs["parent_id"] is None

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_tags_with_search(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags?name=term filters by name."""
        expected_tags = [TagReadFactory(name="action")]
        tag_service_mock.get_tags.return_value = PaginatedResponse[TagRead](
            items=expected_tags, page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/tags?name=action")

        assert response.status_code == HTTPStatus.OK
        tag_service_mock.get_tags.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_tags_empty(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags returns empty list when no tags exist."""
        tag_service_mock.get_tags.return_value = PaginatedResponse[TagRead](
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/tags")

        assert response.status_code == HTTPStatus.OK
        assert response.json()["items"] == []


class TestListChildTags:
    """Tests for GET /api/tags/{tag_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_child_tags_success(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags/{id}/tags returns child tags."""
        child_tags = [TagReadFactory(parent_id=10) for _ in range(2)]
        tag_service_mock.get_tags.return_value = PaginatedResponse[TagRead](
            items=child_tags, page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/tags/10/tags")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 2

        # Verify service called with correct parent_id
        tag_service_mock.get_tags.assert_called_once()
        call_kwargs = tag_service_mock.get_tags.call_args[1]
        assert call_kwargs["parent_id"] == 10

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_child_tags_empty(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags/{id}/tags returns empty list when no children."""
        tag_service_mock.get_tags.return_value = PaginatedResponse[TagRead](
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/tags/5/tags")

        assert response.status_code == HTTPStatus.OK
        assert response.json()["items"] == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_child_tags_parent_not_found(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags/{id}/tags returns 404 when parent not found."""
        from fastapi import HTTPException

        tag_service_mock.get_tags.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Parent tag not found"
        )

        response = client.get("/api/tags/999/tags")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestGetTag:
    """Tests for GET /api/tags/{tag_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_tag_success(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags/{id} returns the tag."""
        expected_tag = TagReadFactory(id=42)
        tag_service_mock.get_tag.return_value = expected_tag

        response = client.get("/api/tags/42")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 42
        assert response_data["name"] == expected_tag.name
        tag_service_mock.get_tag.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_tag_not_found(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/tags/{id} returns 404 when tag not found."""
        from fastapi import HTTPException

        tag_service_mock.get_tag.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Tag not found"
        )

        response = client.get("/api/tags/999")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCreateTag:
    """Tests for POST /api/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_tag_success(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/tags returns 201 and created tag."""
        expected_tag = TagReadFactory()
        tag_service_mock.create_tag.return_value = expected_tag

        payload = get_tag_creation_json(expected_tag)
        response = client.post("/api/tags", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_tag.id
        assert response_data["name"] == expected_tag.name

        # Verify service called with parent_id=None for top-level tag
        tag_service_mock.create_tag.assert_called_once()
        call_args = tag_service_mock.create_tag.call_args[0]
        assert isinstance(call_args[0], TagCreatePublic)
        call_kwargs = tag_service_mock.create_tag.call_args[1]
        assert call_kwargs["parent_id"] is None

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_tag_missing_required_field(self, client: TestClient) -> None:
        """POST /api/tags returns 422 when required field missing."""
        invalid_payload = {"description": "Missing name field"}

        response = client.post("/api/tags", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_tag_invalid_field(self, client: TestClient) -> None:
        """POST /api/tags returns 422 for invalid field."""
        invalid_payload = {"name": "ValidName", "nonexistent_field": "value"}

        response = client.post("/api/tags", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestCreateChildTag:
    """Tests for POST /api/tags/{tag_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_child_tag_success(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/tags/{id}/tags creates child tag."""
        expected_tag = TagReadFactory(parent_id=10)
        tag_service_mock.create_tag.return_value = expected_tag

        payload = get_tag_creation_json(expected_tag)
        response = client.post("/api/tags/10/tags", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["parent_id"] == 10

        # Verify service called with correct parent_id
        tag_service_mock.create_tag.assert_called_once()
        call_kwargs = tag_service_mock.create_tag.call_args[1]
        assert call_kwargs["parent_id"] == 10

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_child_tag_parent_not_found(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/tags/{id}/tags returns 404 when parent not found."""
        from fastapi import HTTPException

        tag_service_mock.create_tag.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Parent tag not found"
        )

        payload = {"name": "ChildTag"}
        response = client.post("/api/tags/999/tags", json=payload)

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestUpdateTag:
    """Tests for PATCH /api/tags/{tag_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_tag_success(self, client: TestClient, tag_service_mock) -> None:
        """PATCH /api/tags/{id} returns 200 and updated tag."""
        updated_tag = TagReadFactory(id=5)
        tag_service_mock.update_tag.return_value = updated_tag

        response = client.patch("/api/tags/5", json={"description": "New description"})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 5

        # Verify service called with exclude_none=True for PATCH
        tag_service_mock.update_tag.assert_called_once()
        call_args = tag_service_mock.update_tag.call_args[0]
        assert call_args[0] == 5  # tag_id
        assert isinstance(call_args[1], TagPatchPublic)
        assert call_args[2] is True  # exclude_none

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_tag_partial_fields(self, client: TestClient, tag_service_mock) -> None:
        """PATCH /api/tags/{id} allows partial updates."""
        updated_tag = TagReadFactory()
        tag_service_mock.update_tag.return_value = updated_tag

        response = client.patch("/api/tags/1", json={"description": "New description"})

        assert response.status_code == HTTPStatus.OK
        tag_service_mock.update_tag.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_tag_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/tags/{id} returns 422 for invalid field."""
        response = client.patch("/api/tags/1", json={"nonexistent_field": "value"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_tag_not_found(self, client: TestClient, tag_service_mock) -> None:
        """PATCH /api/tags/{id} returns 404 when tag not found."""
        from fastapi import HTTPException

        tag_service_mock.update_tag.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Tag not found"
        )

        response = client.patch("/api/tags/999", json={"name": "NewName"})

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestReplaceTag:
    """Tests for PUT /api/tags/{tag_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_replace_tag_success(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/tags/{id} replaces tag with exclude_none=False."""
        replaced_tag = TagReadFactory(id=5)
        tag_service_mock.update_tag.return_value = replaced_tag

        response = client.put(
            "/api/tags/5",
            json={"name": "ReplacedName", "description": "New desc", "color": "#FF0000"},
        )

        assert response.status_code == HTTPStatus.OK

        # Verify service called with exclude_none=False for PUT
        tag_service_mock.update_tag.assert_called_once()
        call_args = tag_service_mock.update_tag.call_args[0]
        assert call_args[2] is False  # exclude_none

    @pytest.mark.unit
    @pytest.mark.api
    def test_replace_tag_not_found(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/tags/{id} returns 404 when tag not found."""
        from fastapi import HTTPException

        tag_service_mock.update_tag.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Tag not found"
        )

        response = client.put("/api/tags/999", json={"name": "NewName"})

        assert response.status_code == HTTPStatus.NOT_FOUND
