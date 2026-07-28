# tests/unit/api/assets/test_files.py
"""Unit tests for asset file/accessory endpoints."""

from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi.testclient import TestClient


class TestListAccessories:
    """Tests for GET /api/assets/{asset_id}/accessories."""

    @pytest.mark.unit
    def test_endpoint_is_sync(self) -> None:
        """The handler does blocking filesystem I/O, so it must stay a plain `def`.

        An `async def` here would run directly on the event loop instead of
        FastAPI's threadpool, blocking every concurrent request during the scan.
        """
        import asyncio

        from app.routers.assets.files import list_accessories

        assert not asyncio.iscoroutinefunction(list_accessories)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_accessories_empty_when_dir_missing(
        self,
        client: TestClient,
        media_service_mock,
    ) -> None:
        """GET /api/assets/{id}/accessories returns empty list when directory doesn't exist."""
        # Mock asset existence check
        media_service_mock.get_asset.return_value = {"id": 1}

        response = client.get("/api/assets/1/accessories")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["items"] == []
        assert response_data["asset_id"] == 1
        media_service_mock.get_asset.assert_called_once_with(1)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_accessories_with_files(
        self,
        client: TestClient,
        media_service_mock,
        accessory_root: Path,
    ) -> None:
        """GET /api/assets/{id}/accessories returns list of files in accessory directory."""
        # Mock asset existence check
        media_service_mock.get_asset.return_value = {"id": 1}

        from app.utils.paths import accessory_relative_path

        # Create accessory directory and files
        acc_rel = accessory_relative_path(1)
        acc_dir = (accessory_root / acc_rel).resolve()
        os.makedirs(acc_dir, exist_ok=True)
        (acc_dir / "subtitle.srt").write_text("1\n00:00:00 --> 00:00:05\nHello")
        (acc_dir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff")  # Fake JPEG header

        response = client.get("/api/assets/1/accessories")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert "items" in response_data
        assert len(response_data["items"]) == 2

        # Verify file info structure
        filenames = {item["filename"] for item in response_data["items"]}
        assert filenames == {"subtitle.srt", "thumbnail.jpg"}

        for item in response_data["items"]:
            assert "size" in item
            assert "mtime" in item
            assert isinstance(item["size"], int)
            assert item["size"] > 0

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_accessories_ignores_subdirectories(
        self, client: TestClient, media_service_mock, accessory_root: Path
    ) -> None:
        """GET /api/assets/{id}/accessories only lists files, not directories."""
        media_service_mock.get_asset.return_value = {"id": 3}

        from app.utils.paths import accessory_relative_path

        # Create accessory directory with file and subdirectory
        acc_rel = accessory_relative_path(3)
        acc_dir = (accessory_root / acc_rel).resolve()
        os.makedirs(acc_dir, exist_ok=True)
        (acc_dir / "file.txt").write_text("content")
        os.makedirs(acc_dir / "subdir", exist_ok=True)

        response = client.get("/api/assets/3/accessories")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert len(response_data["items"]) == 1  # Only the file
        assert response_data["items"][0]["filename"] == "file.txt"

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_accessories_asset_not_found(self, client: TestClient, media_service_mock) -> None:
        """GET /api/assets/{id}/accessories returns 404 when asset doesn't exist."""
        from fastapi import HTTPException

        media_service_mock.get_asset.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Asset not found"
        )

        response = client.get("/api/assets/999/accessories")

        assert response.status_code == HTTPStatus.NOT_FOUND
        media_service_mock.get_asset.assert_called_once_with(999)

    @pytest.mark.unit
    @pytest.mark.api
    def test_list_accessories_permission_error_returns_empty(
        self,
        client: TestClient,
        media_service_mock,
        accessory_root: Path,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """GET /api/assets/{id}/accessories returns empty list on permission error."""
        media_service_mock.get_asset.return_value = {"id": 2}

        # Create directory but mock scandir to raise PermissionError
        from app.utils.paths import accessory_relative_path

        acc_rel = accessory_relative_path(2)
        acc_dir = (accessory_root / acc_rel).resolve()
        os.makedirs(acc_dir, exist_ok=True)

        def mock_scandir(path):
            raise PermissionError("Access denied")

        monkeypatch.setattr(os, "scandir", mock_scandir)

        response = client.get("/api/assets/2/accessories")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["items"] == []  # Treat as empty
