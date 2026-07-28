# tests/unit/api/assets/test_core.py
"""Unit tests for core asset endpoints (CRUD operations)."""

from __future__ import annotations


import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import (
    AssetCreatePublic,
    AssetListParams,
    AssetPatchPublic,
    AssetReadExtended,
    PageInfo,
    PaginatedResponse,
    TitleTypeEnum,
)
from tests.factories import AssetReadFactory, get_asset_creation_json


class TestCreateAsset:
    """Tests for POST /api/assets."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_success(self, client: TestClient, media_service_mock) -> None:
        """POST /api/assets returns 201 and created asset when service succeeds."""
        expected_asset = AssetReadFactory()
        media_service_mock.create_asset.return_value = expected_asset

        payload = get_asset_creation_json(expected_asset)  # type: ignore[arg-type]
        response = client.post("/api/assets", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_asset.id
        assert response_data["filename"] == expected_asset.filename
        assert response_data["path"] == expected_asset.path

        # Verify service called once with correct schema type
        media_service_mock.create_asset.assert_called_once()
        call_arg = media_service_mock.create_asset.call_args[0][0]
        assert isinstance(call_arg, AssetCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_missing_required_field(self, client: TestClient) -> None:
        """POST /api/assets returns 422 when required field missing."""
        invalid_payload = {
            "path": "/media/test.mp4",
            "duration": 10.0,
            "bitrate": 1024,
            "size": 1000,
            # Missing: filename, container_format
        }

        response = client.post("/api/assets", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        response_text = response.text.lower()
        assert "filename" in response_text or "field required" in response_text

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_extra_field_rejected(self, client: TestClient) -> None:
        """POST /api/assets returns 422 when extra field provided."""
        asset = AssetReadFactory()
        payload = get_asset_creation_json(asset)  # type: ignore[arg-type]
        payload_dict = dict(payload)
        payload_dict["unknown_field"] = "should_fail"

        response = client.post("/api/assets", json=payload_dict)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "extra" in response.text.lower() or "permitted" in response.text.lower()

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_asset_invalid_field_type(self, client: TestClient) -> None:
        """POST /api/assets returns 422 when field has wrong type."""
        invalid_payload = {
            "path": "/media/test.mp4",
            "filename": "test.mp4",
            "duration": "not_a_number",  # Should be float
            "bitrate": 1024,
            "container_format": "mp4",
            "size": 1000,
        }

        response = client.post("/api/assets", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestListAssets:
    """Tests for GET /api/assets."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_assets_default_params(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets uses default pagination when no params provided."""
        expected_assets = [AssetReadFactory() for _ in range(3)]
        expected_response = PaginatedResponse[AssetReadExtended](
            items=expected_assets,
            page=PageInfo(next=None, prev=None),
        )
        media_service_mock.get_assets.return_value = expected_response

        response = client.get("/api/assets")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 3
        assert response_data["page"]["next"] is None

        # Verify service called with default params
        media_service_mock.get_assets.assert_called_once()
        call_params = media_service_mock.get_assets.call_args[0][0]
        assert isinstance(call_params, AssetListParams)
        # Default limit from schema
        assert call_params.limit == 50

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_assets_custom_limit(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets?limit=10 passes custom limit to service."""
        media_service_mock.get_assets.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/assets?limit=10")

        assert response.status_code == HTTPStatus.OK
        call_params = media_service_mock.get_assets.call_args[0][0]
        assert call_params.limit == 10

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_assets_with_sort(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets?sort=filename:desc passes sort to service."""
        media_service_mock.get_assets.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/assets?sort=filename:desc")

        assert response.status_code == HTTPStatus.OK
        call_params = media_service_mock.get_assets.call_args[0][0]
        assert call_params.sort == "filename:desc"

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_assets_with_pagination_cursor(
        self, client: TestClient, media_service_mock
    ) -> None:
        """GET /api/assets?after=xyz passes cursor to service."""
        media_service_mock.get_assets.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )

        response = client.get("/api/assets?after=cursor123")

        assert response.status_code == HTTPStatus.OK
        call_params = media_service_mock.get_assets.call_args[0][0]
        assert call_params.after == "cursor123"

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_assets_pagination_response_structure(
        self, client: TestClient, media_service_mock
    ) -> None:
        """GET /api/assets returns proper pagination structure."""
        assets = [AssetReadFactory() for _ in range(2)]
        media_service_mock.get_assets.return_value = PaginatedResponse(
            items=assets,
            page=PageInfo(next="next_cursor", prev="prev_cursor"),
        )

        response = client.get("/api/assets")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert "items" in response_data
        assert "page" in response_data
        assert len(response_data["items"]) == 2
        assert response_data["page"]["next"] == "next_cursor"
        assert response_data["page"]["prev"] == "prev_cursor"


class TestGetAsset:
    """Tests for GET /api/assets/{asset_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_success(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets/{id} returns asset when found."""
        expected_asset = AssetReadFactory(id=42)
        media_service_mock.get_asset.return_value = expected_asset

        response = client.get("/api/assets/42")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 42
        assert response_data["filename"] == expected_asset.filename
        media_service_mock.get_asset.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_not_found(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets/{id} returns 404 when service raises HTTPException."""
        from fastapi import HTTPException

        media_service_mock.get_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999")

        assert response.status_code == HTTPStatus.NOT_FOUND
        media_service_mock.get_asset.assert_called_once_with(999)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_invalid_id_type(self, client: TestClient) -> None:
        """GET /api/assets/{id} returns 404 when id is not an integer (FastAPI behavior)."""
        response = client.get("/api/assets/not_an_int")

        # FastAPI returns 404 for path parameter type mismatches
        assert response.status_code == HTTPStatus.NOT_FOUND


class TestGetAssetByExternalId:
    """Tests for GET /api/assets/by-scheme/{scheme_id}/{external_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_by_external_id_success(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets/by-scheme/{scheme_id}/{external_id} returns asset when found."""
        expected_asset = AssetReadFactory()
        media_service_mock.get_asset_by_external_id.return_value = expected_asset

        response = client.get("/api/assets/by-scheme/1/ext123")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == expected_asset.id
        media_service_mock.get_asset_by_external_id.assert_called_once_with(1, "ext123")

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_by_external_id_not_found(
        self, client: TestClient, media_service_mock
    ) -> None:
        """GET /api/assets/by-scheme/{scheme_id}/{external_id} returns 404 when not found."""
        from fastapi import HTTPException

        media_service_mock.get_asset_by_external_id.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/by-scheme/1/unknown")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestUpdateAsset:
    """Tests for PATCH /api/assets/{asset_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_asset_success(self, client: TestClient, media_service_mock) -> None:
        """PATCH /api/assets/{id} returns 200 and updated asset."""
        updated_asset = AssetReadFactory(id=10, filename="updated.mp4")
        media_service_mock.update_asset.return_value = updated_asset

        response = client.patch("/api/assets/10", json={"filename": "updated.mp4"})

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 10
        assert response_data["filename"] == "updated.mp4"

        # Verify service called with correct parameters
        media_service_mock.update_asset.assert_called_once()
        call_args = media_service_mock.update_asset.call_args
        assert call_args[0][0] == 10  # asset_id
        assert isinstance(call_args[0][1], AssetPatchPublic)  # update object
        assert call_args[1]["exclude_none"] is True  # exclude_none for PATCH
        assert call_args[1]["perform_rename"] is False

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_asset_partial_fields(self, client: TestClient, media_service_mock) -> None:
        """PATCH /api/assets/{id} allows partial updates."""
        updated_asset = AssetReadFactory()
        media_service_mock.update_asset.return_value = updated_asset

        response = client.patch("/api/assets/5", json={"bitrate": 2048})

        assert response.status_code == HTTPStatus.OK
        media_service_mock.update_asset.assert_called_once()
        call_update = media_service_mock.update_asset.call_args[0][1]
        assert call_update.bitrate == 2048

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_asset_invalid_field(self, client: TestClient) -> None:
        """PATCH /api/assets/{id} returns 422 for invalid field."""
        response = client.patch("/api/assets/1", json={"nonexistent_field": "value"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_asset_not_found(self, client: TestClient, media_service_mock) -> None:
        """PATCH /api/assets/{id} returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        media_service_mock.update_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.patch("/api/assets/999", json={"filename": "new.mp4"})

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestGetAssetTitles:
    """Tests for GET /api/assets/{asset_id}/titles."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_titles_success(self, client: TestClient, title_content_service_mock) -> None:
        """GET /api/assets/{asset_id}/titles returns titles when found."""
        from app.schemas import TitleContentReadParent, TitleRead
        from app.models.title_contents import ContentKind

        expected_titles = [
            TitleContentReadParent(
                id=1,
                parent_title_id=10,
                order_key="A",
                kind=ContentKind.asset,
                child_title_id=None,
                asset_id=42,
                label="First",
                parent_title=TitleRead(id=10, name="Title 1", title_type=TitleTypeEnum.movie),
            ),
            TitleContentReadParent(
                id=2,
                parent_title_id=20,
                order_key="B",
                kind=ContentKind.asset,
                child_title_id=None,
                asset_id=42,
                label="Second",
                parent_title=TitleRead(id=20, name="Title 2", title_type=TitleTypeEnum.season),
            ),
        ]
        title_content_service_mock.get_titles_with_asset.return_value = expected_titles

        response = client.get("/api/assets/42/titles")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 2
        assert response_data[0]["parent_title_id"] == 10
        assert response_data[1]["parent_title_id"] == 20
        assert response_data[0]["parent_title"]["name"] == "Title 1"
        assert response_data[1]["parent_title"]["name"] == "Title 2"
        title_content_service_mock.get_titles_with_asset.assert_called_once_with(42)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_titles_empty_list(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """GET /api/assets/{asset_id}/titles returns empty list when asset has no titles."""
        title_content_service_mock.get_titles_with_asset.return_value = []

        response = client.get("/api/assets/99/titles")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 0
        title_content_service_mock.get_titles_with_asset.assert_called_once_with(99)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_titles_not_found(
        self, client: TestClient, title_content_service_mock
    ) -> None:
        """GET /api/assets/{asset_id}/titles returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        title_content_service_mock.get_titles_with_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999/titles")

        assert response.status_code == HTTPStatus.NOT_FOUND
        title_content_service_mock.get_titles_with_asset.assert_called_once_with(999)

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_asset_titles_invalid_id_type(self, client: TestClient) -> None:
        """GET /api/assets/{id}/titles returns 404 when id is not an integer."""
        response = client.get("/api/assets/not_an_int/titles")

        # FastAPI returns 404 for path parameter type mismatches
        assert response.status_code == HTTPStatus.NOT_FOUND


class TestMarkAssetsSeen:
    """Tests for PATCH /api/assets/seen."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_mark_assets_seen_success(self, client: TestClient, media_service_mock) -> None:
        """PATCH /api/assets/seen marks assets as seen."""
        media_service_mock.mark_assets_seen.return_value = 2

        response = client.patch("/api/assets/seen", json={"ids": [1, 2]})

        assert response.status_code in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)
        media_service_mock.mark_assets_seen.assert_called_once_with([1, 2])

    @pytest.mark.unit
    @pytest.mark.api
    def test_mark_assets_seen_empty_list(self, client: TestClient, media_service_mock) -> None:
        """PATCH /api/assets/seen rejects empty list (schema requires min_length=1)."""
        response = client.patch("/api/assets/seen", json={"ids": []})

        # Schema requires at least one ID
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        media_service_mock.mark_assets_seen.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.api
    def test_mark_assets_seen_validation_error(self, client: TestClient) -> None:
        """PATCH /api/assets/seen returns 422 for invalid payload."""
        response = client.patch("/api/assets/seen", json={"ids": "not_a_list"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
