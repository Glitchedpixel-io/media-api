# tests/integration/api/test_inbox_api.py
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.repositories import (
    InboxRepository,
    MediaRepository,
    TransformRequestRepository,
)
from app.schemas.enums import TransformTypeEnum


@pytest.mark.integration
@pytest.mark.api
class TestInboxAPI:
    """Test the /api/inbox API endpoints with full-stack integration."""

    def test_list_add_empty(self, client: TestClient) -> None:
        # Act
        resp = client.get("/api/inbox")

        # Assert
        assert resp.status_code == HTTPStatus.OK
        r = resp.json()
        assert isinstance(r, list)
        assert not r

    def test_list_all(self, client: TestClient, inbox_repository: InboxRepository) -> None:
        # Arrange
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            inbox_tmp_file.write_bytes(b"in")

        # Act
        resp = client.get("/api/inbox")

        # Assert
        assert resp.status_code == HTTPStatus.OK
        r = resp.json()
        assert isinstance(r, list)
        assert len(r) == 5
        assert all(f"clip{i}.avi" in r[i]["name"] for i in range(5))

    def test_delete_one(self, client: TestClient, inbox_repository: InboxRepository) -> None:
        # Arrange
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            inbox_tmp_file.write_bytes(b"in")

        # Act
        resp = client.delete("/api/inbox", params={"source": "clip0.avi"})

        # Assert
        assert resp and resp.status_code == HTTPStatus.NO_CONTENT

        # i.e. four of the files remain
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            assert Path(inbox_tmp_file).exists() == (i != 0)

        # ...while clip0.avi was moved to trash
        assert Path(inbox_repository.inbox_root / ".trash/clip0.avi").exists()  # type: ignore

    def test_delete_non_existent(
        self, client: TestClient, inbox_repository: InboxRepository
    ) -> None:
        # Arrange
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            inbox_tmp_file.write_bytes(b"in")

        # Act
        resp = client.delete("/api/inbox", params={"source": "clip5.avi"})

        # Assert
        assert resp and resp.status_code == HTTPStatus.NOT_FOUND

        # all files remain
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            assert Path(inbox_tmp_file).exists()

    def test_import_file(
        self,
        client: TestClient,
        inbox_repository: InboxRepository,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        # Arrange
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            inbox_tmp_file.write_bytes(b"in")

        # Act
        resp = client.post(
            "/api/inbox",
            json={"source": "clip0.avi", "target": "folder/clip0 (abc).avi"},
        )

        # Assert
        assert resp and resp.status_code == HTTPStatus.CREATED
        r = resp.json()
        assert r["id"] is not None
        assert r["path"] == "folder/clip0 (abc).avi"
        assert r["filename"] == "clip0 (abc).avi"
        assert r["size"] == 2
        asset_id = r["id"]

        # only four of the files remain in the inbox
        for i in range(5):
            inbox_tmp_file = inbox_repository.inbox_root / f"clip{i}.avi"  # type: ignore
            assert Path(inbox_tmp_file).exists() == (i != 0)

        # the other has been moved
        Path(inbox_repository.media_root / "folder/clip0 (abc).avi").exists()  # type: ignore

        # Act 2
        resp = client.get(f"/api/assets/{asset_id}/transform_requests")

        # Assert 2
        assert resp and resp.status_code == HTTPStatus.OK
        r = resp.json()
        assert len(r) == 2
        req = r[0]
        assert req["asset_id"] == asset_id
        assert req["transform_type"] == TransformTypeEnum.stream_reader.value
        assert req["parameters"] == {}
        assert req["actioned"] == False
        assert req["processed_at"] is None
        req = r[1]
        assert req["asset_id"] == asset_id
        assert req["transform_type"] == TransformTypeEnum.ffprobe_metadata.value
        assert req["parameters"] != {}
        assert req["actioned"] == False
        assert req["processed_at"] is None
