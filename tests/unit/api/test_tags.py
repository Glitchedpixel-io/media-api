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

        # The service takes the id and the submitted model, and nothing else. There
        # used to be a third positional argument -- `exclude_none` -- which the PUT
        # route passed as False; both are gone (#181).
        tag_service_mock.update_tag.assert_called_once()
        call_args = tag_service_mock.update_tag.call_args[0]
        assert call_args[0] == 5  # tag_id
        assert isinstance(call_args[1], TagPatchPublic)
        assert len(call_args) == 2

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


class TestTheTagPutRouteIsGone:
    """`PUT /api/tags/{tag_id}` was removed in #181.

    It was bound to `TagPatchPublic` -- the same optional-field model the PATCH uses --
    and called the service with `exclude_none=False`, so omitted fields were written as
    nulls. Measured, that route was unusable as well as unsafe: a body omitting any NOT
    NULL column produced a 422 from Postgres, an empty body raised a ValidationError out
    of the service as a 500, and the one body shape that succeeded silently erased
    `description`.

    405 rather than 404 because the path still exists for GET, PATCH and DELETE; it is
    the method that is gone.
    """

    @pytest.mark.unit
    @pytest.mark.api
    def test_put_is_not_routed(self, client: TestClient) -> None:
        response = client.put(
            "/api/tags/5",
            json={"name": "ReplacedName", "description": "New desc", "color": "#FF0000"},
        )

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    @pytest.mark.unit
    @pytest.mark.api
    def test_patch_still_reaches_every_field(self, client: TestClient, tag_service_mock) -> None:
        """Removing PUT costs no capability: PATCH covers the whole model."""
        tag_service_mock.update_tag.return_value = TagReadFactory(id=5)

        response = client.patch(
            "/api/tags/5",
            json={"name": "NewName", "description": "New desc", "color": "#FF0000"},
        )

        assert response.status_code == HTTPStatus.OK
        submitted = tag_service_mock.update_tag.call_args[0][1]
        assert submitted.name == "newname"
        assert submitted.description == "New desc"
        assert submitted.color == "#FF0000"
