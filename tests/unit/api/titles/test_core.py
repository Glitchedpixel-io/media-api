# tests/unit/api/titles/test_core.py
"""Unit tests for core title endpoints (CRUD operations)."""

from __future__ import annotations


import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import (
    PaginatedResponse,
    PageInfo,
    TitleCreatePublic,
    TitleListParams,
    TitlePatchPublic,
    TitleReadExtended,
)
from tests.factories import TitleReadFactory, get_title_creation_json


class TestCreateTitle:
    """Tests for POST /api/titles."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_title_success(self, client: TestClient, title_service_mock) -> None:
        """POST /api/titles returns 201 and created title when service succeeds."""
        expected_title = TitleReadFactory()
        title_service_mock.create_title.return_value = expected_title

        payload = get_title_creation_json(expected_title)
        response = client.post("/api/titles", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_title.id
        assert response_data["name"] == expected_title.name
        assert response_data["title_type"] == expected_title.title_type

        # Verify service called once with correct schema type
        title_service_mock.create_title.assert_called_once()
        call_arg = title_service_mock.create_title.call_args[0][0]
        assert isinstance(call_arg, TitleCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_title_missing_required_field(self, client: TestClient) -> None:
        """POST /api/titles returns 422 when required field missing."""
        invalid_payload = {
            "title_type": "movie"
            # Missing: name
        }

        response = client.post("/api/titles", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        response_text = response.text.lower()
        assert "name" in response_text or "field required" in response_text

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_title_extra_field_rejected(self, client: TestClient) -> None:
        """POST /api/titles returns 422 when extra field provided."""
        title = TitleReadFactory()
        payload = get_title_creation_json(title)
        payload_dict = dict(payload)
        payload_dict["unknown_field"] = "should_fail"

        response = client.post("/api/titles", json=payload_dict)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "extra" in response.text.lower() or "permitted" in response.text.lower()

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_title_invalid_type(self, client: TestClient) -> None:
        """POST /api/titles returns 422 for invalid title_type."""
        invalid_payload = {
            "name": "Test Title",
            "title_type": "invalid_type",  # Not in enum
        }

        response = client.post("/api/titles", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestListTitles:
    """Tests for GET /api/titles."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_titles_default_params(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles uses default pagination when no params provided."""
        expected_titles = [TitleReadFactory() for _ in range(3)]
        expected_response = PaginatedResponse[TitleReadExtended](
            items=expected_titles,
            page=PageInfo(next=None, prev=None),
        )
        title_service_mock.get_titles.return_value = expected_response

        response = client.get("/api/titles")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 3
        assert response_data["page"]["next"] is None

        # Verify service called with default params
        title_service_mock.get_titles.assert_called_once()
        call_params = title_service_mock.get_titles.call_args[0][0]
        assert isinstance(call_params, TitleListParams)
        assert call_params.limit == 50  # default

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_titles_custom_limit(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles?limit=10 passes custom limit to service."""
        title_service_mock.get_titles.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/titles?limit=10")

        assert response.status_code == HTTPStatus.OK
        call_params = title_service_mock.get_titles.call_args[0][0]
        assert call_params.limit == 10

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_titles_with_sort(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles?sort=name:desc passes sort to service."""
        title_service_mock.get_titles.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/titles?sort=name:desc")

        assert response.status_code == HTTPStatus.OK
        call_params = title_service_mock.get_titles.call_args[0][0]
        assert call_params.sort == "name:desc"

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_titles_with_name_filter(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles?name=alien filters by name."""
        title_service_mock.get_titles.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/titles?name=alien")

        assert response.status_code == HTTPStatus.OK
        call_params = title_service_mock.get_titles.call_args[0][0]
        assert call_params.name == "alien"

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_titles_with_include(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles?include=tags,references includes related data."""
        title_service_mock.get_titles.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/titles?include=tags,references")

        assert response.status_code == HTTPStatus.OK
        call_params = title_service_mock.get_titles.call_args[0][0]
        assert call_params.include == "tags,references"

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_titles_limit_too_high(self, client: TestClient) -> None:
        """GET /api/titles?limit=5000 returns 422 for limit exceeding max."""
        response = client.get("/api/titles?limit=5000")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestGetTitle:
    """Tests for GET /api/titles/{title_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_title_success(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles/{id} returns title when found."""
        expected_title = TitleReadFactory(id=42)
        title_service_mock.get_title.return_value = expected_title

        response = client.get("/api/titles/42")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 42
        assert response_data["name"] == expected_title.name
        title_service_mock.get_title.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_title_not_found(self, client: TestClient, title_service_mock) -> None:
        """GET /api/titles/{id} returns 404 when service raises HTTPException."""
        from fastapi import HTTPException

        title_service_mock.get_title.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.get("/api/titles/999")

        assert response.status_code == HTTPStatus.NOT_FOUND
        title_service_mock.get_title.assert_called_once_with(999)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_title_invalid_id_type(self, client: TestClient) -> None:
        """GET /api/titles/{id} returns 422 when id is not an integer."""
        response = client.get("/api/titles/not_an_int")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestUpdateTitle:
    """Tests for PATCH /api/titles/{title_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_title_success(self, client: TestClient, title_service_mock) -> None:
        """PATCH /api/titles/{id} returns 200 and updated title."""
        updated_title = TitleReadFactory(id=10, name="Updated Title")
        title_service_mock.update_title.return_value = updated_title

        response = client.patch("/api/titles/10", json={"name": "Updated Title"})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 10
        assert response_data["name"] == "Updated Title"

        # Verify service called with correct parameters
        title_service_mock.update_title.assert_called_once()
        call_args = title_service_mock.update_title.call_args[0]
        assert call_args[0] == 10  # title_id
        assert isinstance(call_args[1], TitlePatchPublic)  # update object
        # No third argument. `exclude_none` was one, passed False by the PUT route so
        # that omitted fields were written as nulls; both went in #181.
        assert len(call_args) == 2

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_title_partial_fields(self, client: TestClient, title_service_mock) -> None:
        """PATCH /api/titles/{id} allows partial updates."""
        updated_title = TitleReadFactory()
        title_service_mock.update_title.return_value = updated_title

        response = client.patch("/api/titles/5", json={"release_year": 2024})

        assert response.status_code == HTTPStatus.OK
        title_service_mock.update_title.assert_called_once()
        call_update = title_service_mock.update_title.call_args[0][1]
        assert call_update.release_year == 2024

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_title_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/titles/{id} returns 422 for invalid field."""
        response = client.patch("/api/titles/1", json={"nonexistent_field": "value"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_title_not_found(self, client: TestClient, title_service_mock) -> None:
        """PATCH /api/titles/{id} returns 404 when title doesn't exist."""
        from fastapi import HTTPException

        title_service_mock.update_title.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.patch("/api/titles/999", json={"name": "New Name"})

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestTheTitlePutRouteIsGone:
    """`PUT /api/titles/{title_id}` was removed in #181.

    It was bound to `TitlePatchPublic`, in which every field is optional, so it could
    not express the complete representation a PUT is supposed to carry -- and the
    handler wrote every field whether or not the caller sent it. Measured: a body
    omitting a NOT NULL column produced a 422 from Postgres rather than from
    validation, and a body complete enough to succeed erased `release_year` and
    `synopsis` without mentioning them.

    405 rather than 404 because the path still serves GET, PATCH and DELETE.
    """

    @pytest.mark.unit
    @pytest.mark.api
    def test_put_is_not_routed(self, client: TestClient) -> None:
        response = client.put("/api/titles/10", json={"name": "New Name"})

        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    @pytest.mark.unit
    @pytest.mark.api
    def test_patch_discards_an_explicit_null_rather_than_clearing(
        self, client: TestClient, title_service_mock
    ) -> None:
        """What replaced it, stated plainly.

        PATCH cannot clear an optional field: an explicit null is discarded by the same
        rule that leaves an omitted field alone. That is a real limitation and it is the
        one the API now has -- worth pinning, because the removed PUT was the only way to
        clear one, and it did so by erasing everything else too.
        """
        title_service_mock.update_title.return_value = TitleReadFactory(id=5)

        response = client.patch("/api/titles/5", json={"name": "Title", "synopsis": None})

        assert response.status_code == HTTPStatus.OK
        submitted = title_service_mock.update_title.call_args[0][1]
        assert submitted.name == "Title"
        assert submitted.synopsis is None
        assert "synopsis" not in submitted.model_dump(exclude_none=True)
