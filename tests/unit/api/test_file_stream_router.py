# tests/unit/api/test_file_stream_router.py
"""Unit tests for file streaming router endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import AssetRead
from tests.factories import AssetReadFactory


class TestFetchAsset:
    """Tests for GET /api/fetch/{asset_id}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_full_stream_without_range(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} returns full file content without range header."""
        rel_path = "videos/sample.mp4"
        abs_path = media_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        content = b"0123456789abcdef"
        abs_path.write_bytes(content)

        asset: AssetRead = AssetReadFactory(path=rel_path, filename="sample.mp4", size=len(content))
        media_service_mock.get_asset.return_value = asset

        response = client.get(f"/api/fetch/{asset.id}")

        assert response.status_code == HTTPStatus.OK
        assert response.content == content
        assert response.headers.get("Accept-Ranges") == "bytes"
        assert response.headers.get("Content-Length") == str(len(content))
        # Content type inferred from .mp4 extension
        assert (
            response.headers.get("content-type", "").startswith("video/")
            or response.headers.get("content-type") == "application/octet-stream"
        )
        media_service_mock.get_asset.assert_called_once_with(asset.id)

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_range_request_partial_content(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} with Range header returns partial content."""
        rel_path = "videos/clip.mp4"
        abs_path = media_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        content = b"ABCDEFGHIJ"
        abs_path.write_bytes(content)

        asset: AssetRead = AssetReadFactory(path=rel_path, filename="clip.mp4", size=len(content))
        media_service_mock.get_asset.return_value = asset

        # Request first 4 bytes (0..3)
        headers = {"Range": "bytes=0-3"}
        response = client.get(f"/api/fetch/{asset.id}", headers=headers)

        assert response.status_code == HTTPStatus.PARTIAL_CONTENT
        assert response.content == content[0:4]
        assert response.headers.get("Content-Range") == f"bytes 0-3/{len(content)}"
        assert response.headers.get("Content-Length") == "4"
        assert response.headers.get("Accept-Ranges") == "bytes"

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_range_suffix_last_n_bytes(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} with suffix Range header returns last N bytes."""
        rel_path = "videos/clip2.mp4"
        abs_path = media_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        content = b"ABCDEFGH"
        abs_path.write_bytes(content)

        asset: AssetRead = AssetReadFactory(path=rel_path, filename="clip2.mp4", size=len(content))
        media_service_mock.get_asset.return_value = asset

        headers = {"Range": "bytes=-5"}
        response = client.get(f"/api/fetch/{asset.id}", headers=headers)

        assert response.status_code == HTTPStatus.PARTIAL_CONTENT
        assert response.content == content[-5:]
        start = len(content) - 5
        end = len(content) - 1
        assert response.headers.get("Content-Range") == f"bytes {start}-{end}/{len(content)}"
        assert response.headers.get("Content-Length") == "5"

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_unsatisfiable_range(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} returns 416 for unsatisfiable range."""
        rel_path = "videos/clip3.mp4"
        abs_path = media_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        content = b"XYZ"
        abs_path.write_bytes(content)

        asset: AssetRead = AssetReadFactory(path=rel_path, filename="clip3.mp4", size=len(content))
        media_service_mock.get_asset.return_value = asset

        headers = {"Range": "bytes=10-20"}
        response = client.get(f"/api/fetch/{asset.id}", headers=headers)

        assert response.status_code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
        assert response.headers.get("Content-Range") == f"bytes */{len(content)}"

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_invalid_range_header(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} returns 400 for invalid Range header."""
        rel_path = "videos/clip4.mp4"
        abs_path = media_root / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        content = b"hello"
        abs_path.write_bytes(content)

        asset: AssetRead = AssetReadFactory(path=rel_path, filename="clip4.mp4", size=len(content))
        media_service_mock.get_asset.return_value = asset

        # Invalid range unit (should be "bytes", not "items")
        headers = {"Range": "items=0-2"}
        response = client.get(f"/api/fetch/{asset.id}", headers=headers)

        assert response.status_code == HTTPStatus.BAD_REQUEST

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_file_not_found(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} returns 404 when file doesn't exist."""
        rel_path = "videos/missing.mp4"
        asset: AssetRead = AssetReadFactory(path=rel_path, filename="missing.mp4", size=0)
        media_service_mock.get_asset.return_value = asset

        response = client.get(f"/api/fetch/{asset.id}")

        assert response.status_code == HTTPStatus.NOT_FOUND

    @pytest.mark.unit
    @pytest.mark.api
    def test_fetch_asset_path_traversal_blocked(
        self, client: TestClient, media_service_mock, media_root: Path
    ) -> None:
        """GET /api/fetch/{asset_id} blocks path traversal outside media root."""
        # Attempt to escape the media root with path traversal
        rel_path = "../../secret.txt"
        asset: AssetRead = AssetReadFactory(path=rel_path, filename="secret.txt", size=0)
        media_service_mock.get_asset.return_value = asset

        response = client.get(f"/api/fetch/{asset.id}")

        # Router normalizes and rejects paths outside root with 404
        assert response.status_code == HTTPStatus.NOT_FOUND
