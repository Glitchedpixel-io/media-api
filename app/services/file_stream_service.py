# app/services/file_stream_service.py
from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException

from app.config import MediaConfig
from app.services.media_service import MediaService
from app.utils.paths import to_linux_path


@dataclass(frozen=True)
class FileStreamResult:
    """Everything a router needs to build a ``StreamingResponse``."""

    iterator: Iterator[bytes]
    status_code: int
    media_type: str
    headers: dict[str, str] = field(default_factory=dict)


class FileStreamService:
    """Resolves an asset to its file on disk and streams it, honoring Range requests."""

    def __init__(self, media_service: MediaService, config: MediaConfig) -> None:
        self.media_service = media_service
        self.media_root = config.media_root

    def build_stream(self, asset_id: int, range_header: str | None) -> FileStreamResult:
        """Resolve ``asset_id`` to a file and build a (possibly partial) stream.

        Args:
            asset_id: The asset to stream.
            range_header: The raw HTTP ``Range`` header value, if present.

        Returns:
            A FileStreamResult describing the byte iterator, status code, media
            type, and headers a router should use to build the response.

        Raises:
            HTTPException: 404 if the asset's file doesn't exist or its
                resolved path escapes the media root; 400 for a malformed
                Range header; 416 for a Range that can't be satisfied.
        """
        asset = self.media_service.get_asset(asset_id)
        abs_path = self._safe_join_media_root(asset.path)
        if not abs_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        file_size = abs_path.stat().st_size
        content_type, _ = mimetypes.guess_type(str(abs_path))
        content_type = content_type or "application/octet-stream"
        headers = {"Accept-Ranges": "bytes"}

        if range_header:
            return self._build_range_stream(
                abs_path, range_header, file_size, content_type, headers
            )

        headers["Content-Length"] = str(file_size)
        return FileStreamResult(
            iterator=self._iter_file_range(abs_path, 0, file_size - 1),
            status_code=200,
            media_type=content_type,
            headers=headers,
        )

    def _safe_join_media_root(self, relative_path: str) -> Path:
        media_root = Path(self.media_root).resolve()
        try:
            # Normalize the incoming relative path to Linux, then strip leading separators for join
            normalized = to_linux_path(relative_path) or ""
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve)) from ve
        rel = Path(normalized.lstrip("/"))
        abs_path = (media_root / rel).resolve()
        # Ensure the resolved path is within media_root to prevent traversal
        if media_root not in abs_path.parents and abs_path != media_root:
            raise HTTPException(status_code=404, detail="File not found")
        return abs_path

    def _build_range_stream(
        self,
        abs_path: Path,
        range_header: str,
        file_size: int,
        content_type: str,
        headers: dict[str, str],
    ) -> FileStreamResult:
        try:
            # Example: Range: bytes=0-1023 or bytes=1024- or bytes=-500
            if not range_header.startswith("bytes="):
                raise ValueError
            ranges_spec = range_header.split("=", 1)[1].strip()
            # Only single range supported
            if "," in ranges_spec:
                # We can choose to reject multiple ranges for simplicity
                raise ValueError
            start_str, end_str = (*ranges_spec.split("-", 1), "")[:2]

            if start_str == "":
                # suffix-byte-range-spec: last N bytes
                suffix_length = int(end_str)
                if suffix_length <= 0:
                    raise ValueError
                start = max(file_size - suffix_length, 0)
                end = file_size - 1
            else:
                start = int(start_str)
                end = int(end_str) if end_str else file_size - 1

            if start < 0 or end < start or start >= file_size:
                # Unsatisfiable
                unsatisfiable_headers = {**headers, "Content-Range": f"bytes */{file_size}"}
                raise HTTPException(
                    status_code=416,
                    detail="Requested Range Not Satisfiable",
                    headers=unsatisfiable_headers,
                )

            end = min(end, file_size - 1)
            content_length = end - start + 1
            range_headers = {
                **headers,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length),
            }
            return FileStreamResult(
                iterator=self._iter_file_range(abs_path, start, end),
                status_code=206,
                media_type=content_type,
                headers=range_headers,
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Range header") from None

    @staticmethod
    def _iter_file_range(
        path: Path, start: int, end: int, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        with path.open("rb") as f:
            f.seek(start)
            bytes_to_read = end - start + 1
            while bytes_to_read > 0:
                chunk = f.read(min(chunk_size, bytes_to_read))
                if not chunk:
                    break
                bytes_to_read -= len(chunk)
                yield chunk
