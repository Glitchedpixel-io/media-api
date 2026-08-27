"""Full-stack tests for GET /api/fetch/{asset_id}.

The router and service tests cover Range handling thoroughly, but every one of them
mocks `MediaService.get_asset`, so the step from a *stored* asset row to a file on
disk is never exercised: `assets.path` is relative, and the service joins it onto
`media_root`. That join is the part that breaks in practice -- a probe run pointed at
a real database with no media mounted returns 404 for every fetch, which reads as
broken streaming rather than a missing file.

These tests use a real asset row and a real file, so a path that stops resolving is a
failing test rather than a puzzling 404 in a report.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import MediaRepository
from app.schemas import AssetCreateInternal
from tests.factories import AssetReadFactory

CONTENT = b"0123456789abcdefghij"


def _seed_asset_with_file(
    media_repository: MediaRepository, media_root: Path, rel_path: str
) -> int:
    """Create a file under the media root and an asset row pointing at it.

    Args:
        media_repository: Repository used to persist the row.
        media_root: The configured media root for this test.
        rel_path: Path relative to the media root, as stored on the asset.

    Returns:
        int: The id of the created asset.
    """
    abs_path = media_root / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(CONTENT)

    asset = AssetReadFactory(path=rel_path, filename=Path(rel_path).name, size=len(CONTENT))
    created = media_repository.create(
        AssetCreateInternal(
            **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
        )
    )
    return created.id


@pytest.mark.api
@pytest.mark.integration
class TestFetchAssetFullStack:
    """A stored asset resolves to its file and streams."""

    def test_a_stored_asset_streams_its_file(
        self, client: TestClient, media_repository: MediaRepository, media_root: Path
    ) -> None:
        """The whole path: row -> relative path -> media root -> bytes."""
        asset_id = _seed_asset_with_file(media_repository, media_root, "videos/full.mp4")

        response = client.get(f"/api/fetch/{asset_id}")

        assert response.status_code == HTTPStatus.OK
        assert response.content == CONTENT
        assert response.headers.get("Accept-Ranges") == "bytes"
        assert response.headers.get("Content-Length") == str(len(CONTENT))

    def test_a_stored_asset_honours_a_range_request(
        self, client: TestClient, media_repository: MediaRepository, media_root: Path
    ) -> None:
        """The request a player issues when the viewer seeks."""
        asset_id = _seed_asset_with_file(media_repository, media_root, "videos/seek.mp4")

        response = client.get(f"/api/fetch/{asset_id}", headers={"Range": "bytes=4-9"})

        assert response.status_code == HTTPStatus.PARTIAL_CONTENT
        assert response.content == CONTENT[4:10]
        assert response.headers.get("Content-Range") == f"bytes 4-9/{len(CONTENT)}"

    def test_a_stored_asset_whose_file_is_missing_is_a_404(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """The failure a probe run against an unmounted media root actually hits.

        The row resolves, the file does not. Asserting it here is what makes that
        404 diagnosable rather than ambiguous with a broken endpoint.
        """
        asset = AssetReadFactory(path="videos/never-written.mp4", filename="never-written.mp4")
        created = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        response = client.get(f"/api/fetch/{created.id}")

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_an_unknown_asset_is_a_404(self, client: TestClient) -> None:
        """No row at all, as distinct from a row with no file."""
        assert client.get("/api/fetch/99999999").status_code == HTTPStatus.NOT_FOUND
