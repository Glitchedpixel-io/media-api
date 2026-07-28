# tests/unit/api/assets/test_relationships.py
"""Unit tests for asset relationship endpoints (derived assets)."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from tests.factories import AssetReadFactory


class TestListDerivedAssets:
    """Tests for GET /api/assets/{asset_id}/derived_assets."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_derived_assets_success(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets/{id}/derived_assets returns list of derived assets."""
        derived = [AssetReadFactory(master_asset_id=1) for _ in range(3)]
        media_service_mock.get_derived_assets.return_value = derived

        response = client.get("/api/assets/1/derived_assets")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert isinstance(response_data, list)
        assert len(response_data) == 3
        assert all(item["master_asset_id"] == 1 for item in response_data)
        media_service_mock.get_derived_assets.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_derived_assets_empty(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets/{id}/derived_assets returns empty list when none exist."""
        media_service_mock.get_derived_assets.return_value = []

        response = client.get("/api/assets/1/derived_assets")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []
        media_service_mock.get_derived_assets.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_derived_assets_master_not_found(
        self, client: TestClient, media_service_mock
    ) -> None:
        """GET /api/assets/{id}/derived_assets returns 404 when master asset doesn't exist."""
        from fastapi import HTTPException

        media_service_mock.get_derived_assets.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999/derived_assets")

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestAddDerivedAsset:
    """Tests for PUT /api/assets/{asset_id}/derived_assets/{child_asset_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_derived_asset_success(self, client: TestClient, media_service_mock) -> None:
        """PUT /api/assets/{id}/derived_assets/{child_id} creates relationship."""
        updated_child = AssetReadFactory(id=2, master_asset_id=1)
        media_service_mock.add_derived_asset.return_value = updated_child

        response = client.put("/api/assets/1/derived_assets/2")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["id"] == 2
        assert response_data["master_asset_id"] == 1
        media_service_mock.add_derived_asset.assert_called_once_with(1, 2)

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_derived_asset_master_not_found(
        self, client: TestClient, media_service_mock
    ) -> None:
        """PUT /api/assets/{id}/derived_assets/{child_id} returns 404 when master not found."""
        from fastapi import HTTPException

        media_service_mock.add_derived_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Master asset not found"
        )

        response = client.put("/api/assets/999/derived_assets/2")

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_derived_asset_child_not_found(
        self, client: TestClient, media_service_mock
    ) -> None:
        """PUT /api/assets/{id}/derived_assets/{child_id} returns 404 when child not found."""
        from fastapi import HTTPException

        media_service_mock.add_derived_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Child asset not found"
        )

        response = client.put("/api/assets/1/derived_assets/999")

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    def test_add_derived_asset_self_reference(self, client: TestClient, media_service_mock) -> None:
        """PUT /api/assets/{id}/derived_assets/{child_id} returns 422 for self-reference."""
        from fastapi import HTTPException

        media_service_mock.add_derived_asset.side_effect = HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Asset cannot be its own master",
        )

        response = client.put("/api/assets/1/derived_assets/1")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
