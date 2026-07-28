# tests/unit/api/assets/test_tags.py
"""Unit tests for asset tag endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import TaggingReport
from tests.factories import TagReadFactory


class TestListAssetTags:
    """Tests for GET /api/assets/{asset_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_tags_success(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/assets/{id}/tags returns list of tags."""
        expected_tags = [TagReadFactory() for _ in range(3)]
        tag_service_mock.get_asset_tags.return_value = expected_tags

        response = client.get("/api/assets/1/tags")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 3
        assert {tag["id"] for tag in response_data} == {t.id for t in expected_tags}
        tag_service_mock.get_asset_tags.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_tags_empty(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/assets/{id}/tags returns empty list when no tags."""
        tag_service_mock.get_asset_tags.return_value = []

        response = client.get("/api/assets/1/tags")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []
        tag_service_mock.get_asset_tags.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_asset_tags_not_found(self, client: TestClient, tag_service_mock) -> None:
        """GET /api/assets/{id}/tags returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        tag_service_mock.get_asset_tags.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999/tags")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestSetAssetTags:
    """Tests for PUT /api/assets/{asset_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_asset_tags_success(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/assets/{id}/tags sets tags and returns updated list."""
        expected_tags = [TagReadFactory(id=1), TagReadFactory(id=2)]
        tag_service_mock.tag_asset_with_tag_ids.return_value = expected_tags

        response = client.put("/api/assets/10/tags", json={"tag_ids": [1, 2]})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data) == 2
        assert {tag["id"] for tag in response_data} == {1, 2}

        # Verify service called correctly
        tag_service_mock.tag_asset_with_tag_ids.assert_called_once()
        call_args = tag_service_mock.tag_asset_with_tag_ids.call_args[0]
        assert call_args[0] == 10  # asset_id
        assert call_args[1].tag_ids == [1, 2]  # TagSet object

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_asset_tags_empty_list(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/assets/{id}/tags with empty list removes all tags."""
        tag_service_mock.tag_asset_with_tag_ids.return_value = []

        response = client.put("/api/assets/10/tags", json={"tag_ids": []})

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []
        tag_service_mock.tag_asset_with_tag_ids.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_asset_tags_validation_error(self, client: TestClient) -> None:
        """PUT /api/assets/{id}/tags returns 422 for invalid payload."""
        response = client.put("/api/assets/1/tags", json={"tag_ids": "not_a_list"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_set_asset_tags_asset_not_found(self, client: TestClient, tag_service_mock) -> None:
        """PUT /api/assets/{id}/tags returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        tag_service_mock.tag_asset_with_tag_ids.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.put("/api/assets/999/tags", json={"tag_ids": [1, 2]})

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestAddAssetTagsByName:
    """Tests for POST /api/assets/{asset_id}/tags."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_asset_tags_by_name_success(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/assets/{id}/tags creates/finds tags by name and returns report."""
        tag1 = TagReadFactory(name="action")
        tag2 = TagReadFactory(name="comedy")
        expected_report = TaggingReport(added_tags=[tag1, tag2], tagging_errors=[])
        tag_service_mock.tag_asset_with_tag_names.return_value = expected_report

        response = client.post("/api/assets/1/tags", json={"tag_names": ["action", "comedy"]})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert "added_tags" in response_data
        assert "tagging_errors" in response_data
        assert len(response_data["added_tags"]) == 2
        assert len(response_data["tagging_errors"]) == 0

        # Verify service called correctly
        tag_service_mock.tag_asset_with_tag_names.assert_called_once()
        call_args = tag_service_mock.tag_asset_with_tag_names.call_args[0]
        assert call_args[0] == 1  # asset_id
        assert call_args[1].tag_names == ["action", "comedy"]

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_asset_tags_by_name_with_errors(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/assets/{id}/tags returns report with errors when some tags fail."""
        tag1 = TagReadFactory(name="valid")
        expected_report = TaggingReport(
            added_tags=[tag1], tagging_errors=["invalid_tag: error message"]
        )
        tag_service_mock.tag_asset_with_tag_names.return_value = expected_report

        response = client.post("/api/assets/1/tags", json={"tag_names": ["valid", "invalid_tag"]})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["added_tags"]) == 1
        assert len(response_data["tagging_errors"]) == 1

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_asset_tags_by_name_empty_list(self, client: TestClient, tag_service_mock) -> None:
        """POST /api/assets/{id}/tags with empty list returns empty report."""
        expected_report = TaggingReport(added_tags=[], tagging_errors=[])
        tag_service_mock.tag_asset_with_tag_names.return_value = expected_report

        response = client.post("/api/assets/1/tags", json={"tag_names": []})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["added_tags"] == []
        assert response_data["tagging_errors"] == []

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_asset_tags_by_name_validation_error(self, client: TestClient) -> None:
        """POST /api/assets/{id}/tags returns 422 for invalid payload."""
        response = client.post("/api/assets/1/tags", json={"tag_names": "not_a_list"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestRemoveAssetTag:
    """Tests for DELETE /api/assets/{asset_id}/tags/{tag_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_remove_asset_tag_success(self, client: TestClient, tag_service_mock) -> None:
        """DELETE /api/assets/{id}/tags/{tag_id} removes tag and returns 200."""
        tag_service_mock.untag_asset.return_value = True  # Tag was removed

        response = client.delete("/api/assets/16/tags/13")

        assert response.status_code == HTTPStatus.OK
        tag_service_mock.untag_asset.assert_called_once_with(16, 13)

    @pytest.mark.unit
    @pytest.mark.api
    def test_remove_asset_tag_not_found(self, client: TestClient, tag_service_mock) -> None:
        """DELETE /api/assets/{id}/tags/{tag_id} returns 204 when tag not on asset."""
        tag_service_mock.untag_asset.return_value = False  # Tag was not on asset

        response = client.delete("/api/assets/16/tags/99")

        assert response.status_code == HTTPStatus.NO_CONTENT
        tag_service_mock.untag_asset.assert_called_once_with(16, 99)

    @pytest.mark.unit
    @pytest.mark.api
    def test_remove_asset_tag_asset_not_found(self, client: TestClient, tag_service_mock) -> None:
        """DELETE /api/assets/{id}/tags/{tag_id} returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        tag_service_mock.untag_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.delete("/api/assets/999/tags/1")

        assert response.status_code == HTTPStatus.NOT_FOUND
