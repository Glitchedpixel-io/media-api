"""Unit tests for FileStreamService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.config.schema import MediaConfig
from app.services.file_stream_service import FileStreamService
from app.services.media_service import MediaService
from tests.factories import AssetReadExtendedFactory


@pytest.fixture
def media_service() -> MediaService:
    return create_autospec(MediaService, instance=True)


@pytest.fixture
def config(tmp_path: Path) -> MediaConfig:
    return MediaConfig(
        media_root=str(tmp_path), accessory_root=str(tmp_path), inbox_root=str(tmp_path)
    )


@pytest.fixture
def service(media_service: MediaService, config: MediaConfig) -> FileStreamService:
    return FileStreamService(media_service, config)


def _write_asset(tmp_path: Path, media_service: MediaService, rel_path: str, content: bytes) -> int:
    abs_path = tmp_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    asset = AssetReadExtendedFactory(path=rel_path, filename=Path(rel_path).name, size=len(content))
    media_service.get_asset.return_value = asset
    return asset.id


@pytest.mark.unit
class TestBuildStreamFullContent:
    def test_no_range_header_streams_whole_file(
        self, service: FileStreamService, media_service: MediaService, tmp_path: Path
    ) -> None:
        content = b"0123456789"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        result = service.build_stream(asset_id, None)

        assert result.status_code == 200
        assert b"".join(result.iterator) == content
        assert result.headers["Content-Length"] == str(len(content))
        assert result.headers["Accept-Ranges"] == "bytes"

    def test_missing_file_raises_404(
        self, service: FileStreamService, media_service: MediaService
    ) -> None:
        asset = AssetReadExtendedFactory(path="does/not/exist.mp4", filename="exist.mp4")
        media_service.get_asset.return_value = asset

        with pytest.raises(HTTPException) as exc_info:
            service.build_stream(asset.id, None)

        assert exc_info.value.status_code == 404

    def test_path_traversal_is_rejected_as_404(
        self, service: FileStreamService, media_service: MediaService
    ) -> None:
        asset = AssetReadExtendedFactory(path="../../secret.txt", filename="secret.txt")
        media_service.get_asset.return_value = asset

        with pytest.raises(HTTPException) as exc_info:
            service.build_stream(asset.id, None)

        assert exc_info.value.status_code == 404


@pytest.mark.unit
class TestBuildStreamRangeRequests:
    def test_simple_range_returns_partial_content(
        self, service: FileStreamService, media_service: MediaService, tmp_path: Path
    ) -> None:
        content = b"ABCDEFGHIJ"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        result = service.build_stream(asset_id, "bytes=0-3")

        assert result.status_code == 206
        assert b"".join(result.iterator) == content[0:4]
        assert result.headers["Content-Range"] == f"bytes 0-3/{len(content)}"
        assert result.headers["Content-Length"] == "4"

    def test_open_ended_range_reads_to_eof(
        self, service: FileStreamService, media_service: MediaService, tmp_path: Path
    ) -> None:
        content = b"ABCDEFGHIJ"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        result = service.build_stream(asset_id, "bytes=4-")

        assert result.status_code == 206
        assert b"".join(result.iterator) == content[4:]
        assert result.headers["Content-Range"] == f"bytes 4-{len(content) - 1}/{len(content)}"

    def test_suffix_range_returns_last_n_bytes(
        self, service: FileStreamService, media_service: MediaService, tmp_path: Path
    ) -> None:
        content = b"ABCDEFGH"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        result = service.build_stream(asset_id, "bytes=-5")

        assert result.status_code == 206
        assert b"".join(result.iterator) == content[-5:]
        start, end = len(content) - 5, len(content) - 1
        assert result.headers["Content-Range"] == f"bytes {start}-{end}/{len(content)}"

    def test_suffix_range_larger_than_file_clamps_to_whole_file(
        self, service: FileStreamService, media_service: MediaService, tmp_path: Path
    ) -> None:
        content = b"ABCDEFGH"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        result = service.build_stream(asset_id, "bytes=-999")

        assert result.status_code == 206
        assert b"".join(result.iterator) == content
        assert result.headers["Content-Range"] == f"bytes 0-{len(content) - 1}/{len(content)}"

    def test_unsatisfiable_range_raises_416(
        self, service: FileStreamService, media_service: MediaService, tmp_path: Path
    ) -> None:
        content = b"XYZ"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        with pytest.raises(HTTPException) as exc_info:
            service.build_stream(asset_id, "bytes=10-20")

        assert exc_info.value.status_code == 416
        assert exc_info.value.headers["Content-Range"] == f"bytes */{len(content)}"

    @pytest.mark.parametrize(
        "range_header",
        [
            "items=0-2",  # wrong unit
            "bytes=0-3,5-7",  # multi-range, unsupported
            "bytes=-0",  # zero-length suffix
            "bytes=abc-def",  # non-numeric
        ],
    )
    def test_invalid_range_header_raises_400(
        self,
        service: FileStreamService,
        media_service: MediaService,
        tmp_path: Path,
        range_header: str,
    ) -> None:
        content = b"hello"
        asset_id = _write_asset(tmp_path, media_service, "clip.mp4", content)

        with pytest.raises(HTTPException) as exc_info:
            service.build_stream(asset_id, range_header)

        assert exc_info.value.status_code == 400
