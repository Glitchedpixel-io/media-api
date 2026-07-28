# tests/unit/api/titles/test_tags.py
"""Unit tests for title tagging endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import TaggingReport, TagNameSet, TagSet
from tests.factories import TagReadFactory


class TestGetTitleTags:
    """Tests for GET /api/titles/{title_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_tags_success(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/titles/{id}/tags returns list of tags."""
        expected_tags = [TagReadFactory() for _ in range(3)]
        tag_service_mock.get_title_tags.return_value = expected_tags

        response = client.get("/api/titles/42/tags")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data) == 3
        tag_service_mock.get_title_tags.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_tags_empty(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/titles/{id}/tags returns empty list when no tags."""
        tag_service_mock.get_title_tags.return_value = []

        response = client.get("/api/titles/5/tags")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_tags_title_not_found(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/titles/{id}/tags returns 404 when title not found."""
        from fastapi import HTTPException

        tag_service_mock.get_title_tags.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.get("/api/titles/999/tags")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestSetTitleTags:
    """Tests for PUT /api/titles/{title_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_tags_success(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/titles/{id}/tags replaces tags with new set."""
        expected_tags = [TagReadFactory(id=1), TagReadFactory(id=2)]
        tag_service_mock.tag_title_with_tag_ids.return_value = expected_tags

        response = client.put("/api/titles/10/tags", json={"tag_ids": [1, 2]})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data) == 2

        # Verify service called with correct parameters
        tag_service_mock.tag_title_with_tag_ids.assert_called_once()
        call_args = tag_service_mock.tag_title_with_tag_ids.call_args[0]
        assert call_args[0] == 10  # title_id
        assert isinstance(call_args[1], TagSet)

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_tags_empty_list(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/titles/{id}/tags with empty list clears all tags."""
        tag_service_mock.tag_title_with_tag_ids.return_value = []

        response = client.put("/api/titles/10/tags", json={"tag_ids": []})

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_tags_title_not_found(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/titles/{id}/tags returns 404 when title not found."""
        from fastapi import HTTPException

        tag_service_mock.tag_title_with_tag_ids.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.put("/api/titles/999/tags", json={"tag_ids": [1, 2]})

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_tags_invalid_tag_id(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/titles/{id}/tags returns 404 when tag ID doesn't exist."""
        from fastapi import HTTPException

        tag_service_mock.tag_title_with_tag_ids.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Tag not found"
        )

        response = client.put("/api/titles/10/tags", json={"tag_ids": [999]})

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestAddTitleTagsByName:
    """Tests for POST /api/titles/{title_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_tags_by_name_success(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/titles/{id}/tags creates/finds tags by name and applies them."""
        report = TaggingReport(
            added_tags=[TagReadFactory(name="action"), TagReadFactory(name="drama")],
            tagging_errors=[],
        )
        tag_service_mock.tag_title_with_tag_names.return_value = report

        response = client.post(
            "/api/titles/10/tags",
            json={"tag_names": ["action", "drama"]},
        )

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["added_tags"]) == 2
        assert response_data["tagging_errors"] == []

        # Verify service called with correct parameters
        tag_service_mock.tag_title_with_tag_names.assert_called_once()
        call_args = tag_service_mock.tag_title_with_tag_names.call_args[0]
        assert call_args[0] == 10  # title_id
        assert isinstance(call_args[1], TagNameSet)

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_tags_by_name_all_new(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/titles/{id}/tags creates new tags when none exist."""
        report = TaggingReport(
            added_tags=[TagReadFactory(name="new_tag")],
            tagging_errors=[],
        )
        tag_service_mock.tag_title_with_tag_names.return_value = report

        response = client.post(
            "/api/titles/10/tags",
            json={"tag_names": ["new_tag"]},
        )

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["added_tags"]) == 1
        assert response_data["tagging_errors"] == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_tags_by_name_empty_list(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/titles/{id}/tags with empty tag_names passes empty set to service."""
        from app.schemas import TaggingReport

        report = TaggingReport(added_tags=[], tagging_errors=[])
        tag_service_mock.tag_title_with_tag_names.return_value = report

        response = client.post(
            "/api/titles/10/tags",
            json={"tag_names": []},
        )

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["added_tags"] == []
        assert response_data["tagging_errors"] == []
        tag_service_mock.tag_title_with_tag_names.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_tags_by_name_title_not_found(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/titles/{id}/tags returns 404 when title not found."""
        from fastapi import HTTPException

        tag_service_mock.tag_title_with_tag_names.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.post(
            "/api/titles/999/tags",
            json={"tag_names": ["action"]},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestUntagTitle:
    """Tests for DELETE /api/titles/{title_id}/tags/{tag_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_untag_success(self, client: TestClient, tag_service_mock) -> None:
        """DELETE /api/titles/{id}/tags/{tag_id} returns 200 when tag removed."""
        tag_service_mock.untag_title.return_value = True

        response = client.delete("/api/titles/10/tags/5")

        assert response.status_code == HTTPStatus.OK
        tag_service_mock.untag_title.assert_called_once_with(10, 5)

    @pytest.mark.unit
    @pytest.mark.api
    def test_untag_already_removed(self, client: TestClient, tag_service_mock) -> None:
        """DELETE /api/titles/{id}/tags/{tag_id} returns 204 when tag wasn't present."""
        tag_service_mock.untag_title.return_value = False

        response = client.delete("/api/titles/10/tags/5")

        assert response.status_code == HTTPStatus.NO_CONTENT
        tag_service_mock.untag_title.assert_called_once_with(10, 5)

    @pytest.mark.unit
    @pytest.mark.api
    def test_untag_title_not_found(self, client: TestClient, tag_service_mock) -> None:
        """DELETE /api/titles/{id}/tags/{tag_id} returns 404 when title not found."""
        from fastapi import HTTPException

        tag_service_mock.untag_title.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Title not found"
        )

        response = client.delete("/api/titles/999/tags/5")

        assert response.status_code == HTTPStatus.NOT_FOUND
