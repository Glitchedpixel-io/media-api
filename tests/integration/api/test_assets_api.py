"""
FastAPI Integration Tests for Assets API

Tests the complete request flow:
API Request → Router → Service → Repository → Database → Repository → Service → Router → API Response

These tests verify:
- HTTP request/response handling
- Request validation and serialization
- Service layer business logic
- Database persistence and queries
- Error handling and status codes
- Full data flow through all application layers
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from http import HTTPStatus

from app.config import AppConfig
from app.repositories.protocols import (
    MediaRepository,
    StreamRepository,
    TagRepository,
    TransformRequestRepository,
)
from app.schemas import (
    AssetCreateInternal,
    OutcomeEnum,
    StreamCreateInternal,
    TagCreateInternal,
    TransformRequestCreateInternal,
)
from tests.factories import (
    AssetReadFactory,
    StreamReadFactory,
    TagReadFactory,
    TransformRequestReadFactory,
    get_asset_creation_json,
)


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPI:
    """Test the /assets API endpoints with full-stack integration."""

    @staticmethod
    def _collect_all_pages(client: TestClient, base_query: str) -> tuple[list[dict], set[int]]:
        """Walk forward through every page, following `page.next`.

        Terminates on a null cursor, which is the documented contract and, since #66,
        the actual behaviour: `page.next` is null exactly when there is no further
        page. This used to stop on an empty page or a cursor that stopped advancing
        as well, because sqlakeyset's marker was serialised unconditionally and the
        cursor was never null -- those conditions are unreachable now and described a
        contract that no longer exists.

        Args:
            client: Test client.
            base_query: Endpoint and query string, without a cursor.

        Returns:
            A tuple of (every item across all pages, the set of ids seen).

        Raises:
            AssertionError: If the walk does not terminate within the cap. That
                guards against a regression in the #66 contract, not against
                expected behaviour -- without it such a regression hangs the suite
                instead of failing it.
        """
        all_items: list[dict] = []
        seen_ids: set[int] = set()
        after: str | None = None

        for _ in range(1000):
            query = base_query
            if after:
                joiner = "&" if "?" in query else "?"
                query = f"{query}{joiner}after={after}"

            resp = client.get(query)
            assert resp.status_code == HTTPStatus.OK
            payload = resp.json()

            all_items.extend(payload["items"])
            seen_ids.update(item["id"] for item in payload["items"])

            after = payload["page"]["next"]
            if after is None:
                break
        else:
            raise AssertionError("Exceeded max pagination steps; cursor likely not advancing.")

        return all_items, seen_ids

    def test_create_asset_success(self, client: TestClient, db_session: Session) -> None:
        """Test successful asset creation through full API stack."""
        # Arrange
        asset = AssetReadFactory()
        asset_data = get_asset_creation_json(asset)  # type: ignore

        # Act
        response = client.post("/api/assets", json=asset_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        r = response.json()

        # Verify response structure and data
        assert "path" in r and r["path"] == asset.path
        assert "filename" in r and r["filename"] == asset.filename
        assert "duration" in r and r["duration"] == asset.duration
        assert "bitrate" in r and r["bitrate"] == asset.bitrate
        assert "container_format" in r and r["container_format"] == asset.container_format
        assert "size" in r and r["size"] == asset.size
        assert "mtime" in r and r["mtime"] is None
        assert "master_asset_id" in r and r["master_asset_id"] is None
        assert "id" in r and r["id"] > 0
        assert "created_at" in r and r["created_at"] is not None

        # Verify database persistence
        db_session.commit()  # Commit to ensure data is persisted
        # Query database directly to verify persistence
        from app.models import AssetORM

        db_asset = db_session.query(AssetORM).filter_by(path=asset.path).first()
        assert db_asset is not None
        assert db_asset.id == r["id"]

    def test_create_asset_validation_error(self, client: TestClient) -> None:
        """Test asset creation with invalid data returns validation error."""
        # Arrange - missing required fields
        invalid_asset_data = {
            "filename": "test.mp4",
            # missing path, file_hash, size, etc.
        }

        # Act
        response = client.post("/api/assets", json=invalid_asset_data)

        # Assert
        assert (
            response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        )  # Unprocessable Entity (validation error)
        assert "detail" in response.json()

    def test_get_assets_empty_list(self, client: TestClient) -> None:
        """Test retrieving assets when none exist."""
        res = client.get("/api/assets")
        assert res.status_code == HTTPStatus.OK
        r = res.json()
        assert "items" in r and r["items"] == []
        # new cursor shape, no offset/meta.total anymore
        assert "page" in r and isinstance(r["page"], dict)
        assert r["page"].get("next") in (
            None,
            "",
            ">",
        )  # implementation may choose None or ""
        assert r["page"].get("prev") in (None, "", "<")

    def test_get_assets_with_data(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving assets after creating some through repository."""
        # Arrange
        asset1 = AssetReadFactory()
        asset2 = AssetReadFactory()
        media_repository.create(
            AssetCreateInternal(
                **asset1.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        media_repository.create(
            AssetCreateInternal(
                **asset2.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Act (explicit sort to make expectation clear)
        response = client.get("/api/assets?limit=50&sort=created_at:desc,id:asc")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()

        assert "items" in r and isinstance(r["items"], list)
        items = r["items"]
        assert len(items) == 2
        assert {asset.path for asset in [asset1, asset2]} == {a["path"] for a in items}

        # New pagination shape
        assert "page" in r and isinstance(r["page"], dict)
        # For a single page of two items, next may be None; prev should be None
        # assert r["page"].get("prev") in (None, "", ">")

    def test_get_assets_pagination(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test cursor pagination parameters."""
        # Arrange
        for i in range(5):
            asset = AssetReadFactory()
            media_repository.create(
                AssetCreateInternal(
                    size=i,
                    **asset.model_dump(exclude={"id", "created_at", "master_asset_id", "size"}),  # type: ignore
                )
            )

        # First page
        resp1 = client.get("/api/assets?limit=2&sort=size:desc,id:asc")
        assert resp1.status_code == HTTPStatus.OK
        p1 = resp1.json()
        assert len(p1["items"]) <= 2
        assert "page" in p1 and isinstance(p1["page"], dict)
        next1 = p1["page"]["next"]

        # Second page via `after`
        resp2 = client.get(f"/api/assets?limit=2&sort=size:desc,id:asc&after={next1}")
        assert resp2.status_code == HTTPStatus.OK
        p2 = resp2.json()
        assert len(p2["items"]) <= 2

        # No overlap between page1 and page2
        ids1 = {i["id"] for i in p1["items"]}
        ids2 = {i["id"] for i in p2["items"]}
        assert ids1.isdisjoint(ids2)

        # Walk all pages to confirm we saw exactly 5
        all_items, seen = self._collect_all_pages(
            client, "/api/assets?limit=2&sort=size:desc,id:asc"
        )
        assert len(seen) == 5

    def test_get_assets_pagination_with_tags(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        tag_repository: TagRepository,
    ) -> None:
        """Test cursor pagination with include=tags."""
        # Arrange: 5 assets, each tagged
        tag = tag_repository.create(
            TagCreateInternal(
                **TagReadFactory().model_dump(exclude={"id", "created_at", "updated_at"})  # type: ignore
            )
        )
        for i in range(5):
            asset = media_repository.create(
                AssetCreateInternal(
                    size=i,
                    **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id", "size"}),  # type: ignore
                )
            )
            tag_repository.add_asset_tags(asset.id, [tag.id])

        # First page with tags
        r_tags = client.get("/api/assets?limit=2&sort=size:desc,id:asc&include=tags")
        assert r_tags.status_code == HTTPStatus.OK
        payload = r_tags.json()
        assert len(payload["items"]) <= 2
        assert all(isinstance(item.get("tags"), list) for item in payload["items"])
        assert all(len(item["tags"]) == 1 for item in payload["items"])

        # Same query without tags
        r_no = client.get("/api/assets?limit=2&sort=size:desc,id:asc")
        assert r_no.status_code == HTTPStatus.OK
        payload_no = r_no.json()
        assert len(payload_no["items"]) <= 2
        assert all(not item.get("tags") for item in payload_no["items"])

        # Walk all with tags and verify count
        all_items, seen = self._collect_all_pages(
            client, "/api/assets?limit=2&sort=size:desc,id:asc&include=tags"
        )
        assert len(seen) == 5
        assert all(isinstance(i.get("tags"), list) for i in all_items)

    def test_get_asset_by_id_success(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving a specific asset by ID."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Act
        response = client.get(f"/api/assets/{asset_id}")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()

        assert "id" in r and r["id"] == asset_id
        assert "filename" in r and r["filename"] == asset.filename
        assert "path" in r and r["path"] == asset.path
        assert "created_at" in r
        assert "master_asset_id" in r and r["master_asset_id"] is None

    def test_get_asset_by_id_not_found(self, client: TestClient) -> None:
        """Test retrieving non-existent asset returns 404."""
        # Act
        response = client.get("/api/assets/99999")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "detail" in response.json()

    def test_update_asset_success(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test updating an asset through the API."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        update_data = {
            "path": "new/path/updated_filename.mp4",
            "filename": "updated_filename.mp4",
            "last_seen": "2025-09-25T15:41:00Z",
        }

        # Act
        response = client.patch(f"/api/assets/{asset_id}", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()

        assert "id" in r and r["id"] == asset_id
        assert "filename" in r and r["filename"] == update_data["filename"]
        assert "path" in r and r["path"] == update_data["path"]
        assert "created_at" in r
        assert "master_asset_id" in r and r["master_asset_id"] is None
        # Verify unchanged fields remain the same
        assert "duration" in r and r["duration"] == asset.duration
        assert "bitrate" in r and r["bitrate"] == asset.bitrate
        assert "container_format" in r and r["container_format"] == asset.container_format
        assert "size" in r and r["size"] == asset.size
        assert "mtime" in r and r["mtime"] is None

    def test_update_asset_not_found(self, client: TestClient) -> None:
        """Test updating non-existent asset returns 404."""
        # Arrange
        update_data = {"filename": "new_name.mp4"}

        # Act
        response = client.patch("/api/assets/99999", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_get_asset_streams(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """Test retrieving streams for a specific asset."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create streams for the asset
        stream1 = StreamReadFactory(asset_id=asset_id)
        stream2 = StreamReadFactory(asset_id=asset_id)
        stream_repository.create(
            StreamCreateInternal(**stream1.model_dump(exclude={"id"}))  # type: ignore
        )
        stream_repository.create(
            StreamCreateInternal(**stream2.model_dump(exclude={"id"}))  # type: ignore
        )

        # Act
        response = client.get(f"/api/assets/{asset_id}/streams")

        # Assert
        assert response.status_code == HTTPStatus.OK
        streams = response.json()

        assert len(streams) == 2
        assert all(stream["asset_id"] == asset_id for stream in streams)

    def test_create_asset_stream(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test creating a stream for an asset through API."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        stream_data = {
            "stream_index": 4,
            "codec_name": "h264",
            "codec_type": "video",
            "width": 1920,
            "height": 1080,
            "frame_rate": 30.0,
        }

        # Act
        response = client.post(f"/api/assets/{asset_id}/streams", json=stream_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        r = response.json()

        assert r["asset_id"] == asset_id
        assert r["stream_index"] == stream_data["stream_index"]
        assert r["codec_name"] == stream_data["codec_name"]
        assert r["codec_type"] == stream_data["codec_type"]
        assert r["width"] == stream_data["width"]
        assert r["height"] == stream_data["height"]
        assert r["frame_rate"] == stream_data["frame_rate"]
        assert "id" in r and r["id"] > 0

    def test_delete_asset_streams(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """Test deleting all streams for an asset."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create some streams
        stream1 = StreamReadFactory(asset_id=asset_id)
        stream2 = StreamReadFactory(asset_id=asset_id)
        stream_repository.create(
            StreamCreateInternal(**stream1.model_dump(exclude={"id"}))  # type: ignore
        )
        stream_repository.create(
            StreamCreateInternal(**stream2.model_dump(exclude={"id"}))  # type: ignore
        )

        # Act
        response = client.delete(f"/api/assets/{asset_id}/streams")

        # Assert
        assert response.status_code == HTTPStatus.NO_CONTENT

        # Verify streams were deleted
        get_response = client.get(f"/api/assets/{asset_id}/streams")
        assert get_response.status_code == HTTPStatus.OK
        assert len(get_response.json()) == 0

    def test_add_derived_asset(self, client: TestClient, media_repository: MediaRepository) -> None:
        """Test setting one asset as derived from another."""
        # Arrange
        parent_asset = AssetReadFactory()
        created_parent_asset = media_repository.create(
            AssetCreateInternal(
                **parent_asset.model_dump(  # type: ignore
                    exclude={"id", "created_at", "master_asset_id"}
                )
            )
        )
        parent_id = created_parent_asset.id

        # Arrange
        child_asset = AssetReadFactory()
        created_child_asset = media_repository.create(
            AssetCreateInternal(
                **child_asset.model_dump(  # type: ignore
                    exclude={"id", "created_at", "master_asset_id"}
                )
            )
        )
        child_asset_id = created_child_asset.id

        # Act
        response = client.put(f"/api/assets/{parent_id}/derived_assets/{child_asset_id}")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert "id" in r and r["id"] == child_asset_id
        assert "master_asset_id" in r and r["master_asset_id"] == parent_id

        # Act
        response = client.get(f"/api/assets/{parent_id}/derived_assets")
        # Assert
        assert response.status_code == HTTPStatus.OK
        assert isinstance(response.json(), list)
        derived_assets = response.json()
        assert len(derived_assets) == 1
        assert derived_assets[0]["id"] == child_asset_id

    def test_get_derived_assets(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving derived assets for a parent asset."""
        # Arrange
        parent_asset = AssetReadFactory()
        created_parent_asset = media_repository.create(
            AssetCreateInternal(
                **parent_asset.model_dump(  # type: ignore
                    exclude={"id", "created_at", "master_asset_id"}
                )
            )
        )
        parent_id = created_parent_asset.id

        # Create derived assets
        derived_asset1 = AssetReadFactory(master_asset_id=parent_id)
        derived_asset2 = AssetReadFactory(master_asset_id=parent_id)
        media_repository.create(
            AssetCreateInternal(
                **derived_asset1.model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )
        media_repository.create(
            AssetCreateInternal(
                **derived_asset2.model_dump(exclude={"id", "created_at"})  # type: ignore
            )
        )

        # Act
        response = client.get(f"/api/assets/{parent_id}/derived_assets")

        # Assert
        assert response.status_code == HTTPStatus.OK
        derived_assets = response.json()

        assert len(derived_assets) == 2
        assert all(asset["master_asset_id"] == parent_id for asset in derived_assets)

    def test_get_asset_transform_requests(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        """Test retrieving transform requests for an asset."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create transform requests
        transform1 = TransformRequestReadFactory(asset_id=asset_id, transform_type="prefect.test")
        transform2 = TransformRequestReadFactory(
            asset_id=asset_id, transform_type="prefect.youtube"
        )
        transform_request_repository.create(
            TransformRequestCreateInternal(
                **transform1.model_dump(exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"})  # type: ignore
            )
        )
        transform_request_repository.create(
            TransformRequestCreateInternal(
                **transform2.model_dump(exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"})  # type: ignore
            )
        )

        # Act
        response = client.get(f"/api/assets/{asset_id}/transform_requests")

        # Assert
        assert response.status_code == HTTPStatus.OK
        requests = response.json()

        assert len(requests) == 2
        assert all(req["asset_id"] == asset_id for req in requests)

    def test_create_transform_request(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test creating a transform request for an asset."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        transform_data = {
            "transform_type": "prefect.transcode",
            "parameters": {"format": "mp4", "bitrate": "1000k"},
        }

        # Act
        response = client.post(f"/api/assets/{asset_id}/transform_requests", json=transform_data)

        # Assert
        assert response.status_code == HTTPStatus.CREATED
        r = response.json()

        assert r["asset_id"] == asset_id
        assert r["transform_type"] == transform_data["transform_type"]
        assert r["parameters"] == transform_data["parameters"]
        assert r["actioned"] == False
        assert r["processed_at"] is None
        assert r["worker_notes"] is None
        assert r["duration"] is None
        assert r["outcome"] is None
        assert r["worker"] is None
        assert "id" in r and r["id"] > 0
        assert "created_at" in r

    def test_transform_request_retry(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        """Test retrying an actioned transform request."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id
        # Create the first request
        transform = TransformRequestReadFactory(
            asset_id=asset_id,
            transform_type="prefect.test",
            actioned=True,
            worker="test",
            worker_notes="test notes",
            processed_at=datetime.now(UTC),
            outcome=OutcomeEnum.succeeded,
            duration=10,
        )
        tr = transform_request_repository.create(
            TransformRequestCreateInternal(
                **transform.model_dump(exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"})  # type: ignore
            )
        )

        # Act
        response = client.patch(f"/api/transform_requests/{tr.id}/retry")
        assert response and response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["asset_id"] == asset_id
        assert r["transform_type"] == transform.transform_type
        assert r["parameters"] == transform.parameters
        assert r["actioned"] == False
        assert r["processed_at"] is None
        assert r["worker_notes"] is None
        assert r["worker"] is None
        assert r["parent_transform_request_id"] == tr.id
        assert "id" in r and r["id"] > 0 and r["id"] != tr.id

    def test_transform_request_retry_not_allowed(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        """Test retrying an actioned transform request."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id
        # Create the first request
        transform = TransformRequestReadFactory(
            asset_id=asset_id,
            transform_type="prefect.test",
            actioned=False,
        )
        tr = transform_request_repository.create(
            TransformRequestCreateInternal(
                **transform.model_dump(exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"})  # type: ignore
            )
        )

        # Act
        response = client.patch(f"/api/transform_requests/{tr.id}/retry")
        resposne_2 = client.patch(f"/api/transform_requests/{tr.id + 1}/retry")

        # Assert
        assert response and response.status_code == HTTPStatus.CONFLICT
        assert resposne_2 and resposne_2.status_code == HTTPStatus.NOT_FOUND

        # Arrange phase 2

        # Create a completed request of the same type
        transform2 = TransformRequestReadFactory(
            asset_id=asset_id,
            transform_type="prefect.test",
            actioned=True,
            worker="test",
            worker_notes="test notes",
            processed_at=datetime.now(UTC),
            outcome=OutcomeEnum.succeeded,
            duration=10,
        )
        tr2 = transform_request_repository.create(
            TransformRequestCreateInternal(
                **transform2.model_dump(exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"})  # type: ignore
            )
        )

        # Act phase 2
        response = client.patch(f"/api/transform_requests/{tr2.id}/retry")

        # Assert phase 2 - should not be allowed because there is already an open request of this type for this asset
        assert response and response.status_code == HTTPStatus.CONFLICT

    def test_create_linked_transform_request(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        """Test creating a transform request linked to a parent."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id
        # Create the first request
        transform = TransformRequestReadFactory(
            asset_id=asset_id,
            transform_type="prefect.test",
            actioned=True,
            worker="test",
            worker_notes="test notes",
            processed_at=datetime.now(UTC),
            outcome=OutcomeEnum.succeeded,
            duration=10,
        )
        tr = transform_request_repository.create(
            TransformRequestCreateInternal(
                **transform.model_dump(exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"})  # type: ignore
            )
        )
        # parameters for the second requestq
        transform_data = {
            "transform_type": "prefect.transcode",
            "parameters": {"format": "mp4", "bitrate": "1000k"},
        }

        # Act
        response = client.post(f"/api/transform_requests/{tr.id}/link", json=transform_data)
        assert response and response.status_code == HTTPStatus.CREATED
        r = response.json()
        assert r["asset_id"] == asset_id
        assert r["transform_type"] == transform_data["transform_type"]
        assert r["parameters"] == transform_data["parameters"]
        assert r["actioned"] == False
        assert r["processed_at"] is None
        assert r["worker_notes"] is None
        assert r["duration"] is None
        assert r["outcome"] is None
        assert r["worker"] is None
        assert r["parent_transform_request_id"] == tr.id
        assert "id" in r and r["id"] > 0

    def test_get_asset_accessories(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving accessory files for an asset."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Act
        response = client.get(f"/api/assets/{asset_id}/accessories")

        # Assert
        assert response.status_code == HTTPStatus.OK
        response_data = response.json()

        # Should return empty list if no accessories exist
        assert "items" in response_data
        assert "asset_id" in response_data
        assert response_data["asset_id"] == asset_id
        assert isinstance(response_data["items"], list)

    @pytest.mark.parametrize("missing_field", ["filename", "path", "duration", "size"])
    def test_create_asset_missing_required_fields(
        self, client: TestClient, missing_field: str
    ) -> None:
        """Test that missing required fields cause validation errors."""
        # Arrange - create complete asset data then remove one field
        complete_data = {
            "filename": "test.mp4",
            "path": "/media/test.mp4",
            "size": 1024000,
            "duration": 1000,
            "container_format": "mp4",
        }
        incomplete_data = {k: v for k, v in complete_data.items() if k != missing_field}

        # Act
        response = client.post("/api/assets", json=incomplete_data)

        # Assert
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        error_detail = response.json()["detail"]
        # Verify the missing field is mentioned in the error
        assert any(missing_field in str(error).lower() for error in error_detail)


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPIErrorCases:
    """Test error cases and edge conditions for the Assets API."""

    def test_asset_not_found_error_handling(self, client: TestClient) -> None:
        """Test that 404 errors are properly handled across endpoints."""
        non_existent_id = 99999

        # Test various endpoints that should return 404
        endpoints_to_test = [
            f"/api/assets/{non_existent_id}",
            f"/api/assets/{non_existent_id}/streams",
            f"/api/assets/{non_existent_id}/derived_assets",
            f"/api/assets/{non_existent_id}/transform_requests",
            f"/api/assets/{non_existent_id}/accessories",
        ]

        for endpoint in endpoints_to_test:
            response = client.get(endpoint)
            assert (
                response.status_code == HTTPStatus.NOT_FOUND
            ), f"Endpoint {endpoint} should return 404"
            assert "detail" in response.json()

    def test_invalid_asset_id_types(self, client: TestClient) -> None:
        """Test that invalid asset ID types return appropriate errors."""
        invalid_ids = ["abc", "12.5", "null"]

        for invalid_id in invalid_ids:
            response = client.get(f"/api/assets/{invalid_id}")
            # Should return 422 (validation error) or 404 depending on FastAPI version
            assert response.status_code in [HTTPStatus.NOT_FOUND, HTTPStatus.UNPROCESSABLE_ENTITY]

    def test_malformed_json_requests(self, client: TestClient) -> None:
        """Test handling of malformed JSON in POST/PATCH requests."""
        # Test malformed JSON
        response = client.post(
            "/api/assets",
            content='{"filename": "test.mp4", invalid json}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_large_request_handling(self, client: TestClient) -> None:
        """Test handling of very large request bodies."""
        # Create asset data with very large string values
        large_asset_data = {
            "filename": "x" * 10000,  # Very long filename
            "path": "/media/" + "x" * 10000,
            "duration": 1000,
            "size": 1024000,
            "container_format": "mp4",
        }

        response = client.post("/api/assets", json=large_asset_data)
        # Should either succeed or fail gracefully (depending on validation rules)
        assert response.status_code in [HTTPStatus.OK, HTTPStatus.UNPROCESSABLE_ENTITY]


@pytest.mark.integration
def test_patch_assets_seen(
    client: TestClient, media_repository: MediaRepository, db_session: Session
) -> None:
    # Arrange: create two assets
    a1 = media_repository.create(AssetCreateInternal(**AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})))  # type: ignore
    a2 = media_repository.create(AssetCreateInternal(**AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})))  # type: ignore
    assert a1.last_seen is None and a2.last_seen is None

    # Act: call the new endpoint
    resp = client.patch("/api/assets/seen", json={"ids": [a1.id, a2.id]})
    assert resp.status_code in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)

    # Assert: last_seen updated in DB
    from app.models import AssetORM

    db_session.expire_all()
    r1 = db_session.get(AssetORM, a1.id)
    r2 = db_session.get(AssetORM, a2.id)
    assert r1 and r1.last_seen is not None
    assert r2 and r2.last_seen is not None


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPIPerformance:
    """Performance and stress tests for the Assets API."""

    def test_bulk_asset_creation_performance(self, client: TestClient) -> None:
        """Test creating many assets in sequence."""
        import time

        start_time = time.time()
        created_assets = []

        # Create 50 assets
        for i in range(50):
            asset_data = get_asset_creation_json(AssetReadFactory())  # type: ignore

            response = client.post("/api/assets", json=asset_data)
            assert response.status_code == HTTPStatus.CREATED
            created_assets.append(response.json()["id"])

        end_time = time.time()
        duration = end_time - start_time

        # Should complete in reasonable time (adjust threshold as needed)
        assert duration < 30, f"Bulk creation took {duration}s, expected < 30s"
        assert len(created_assets) == 50

    def test_concurrent_asset_access(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test accessing the same asset concurrently."""
        # Arrange - create an asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Act - make multiple concurrent requests
        import concurrent.futures

        def make_request():  # type: ignore
            return client.get(f"/api/assets/{asset_id}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(20)]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]

        # Assert - all requests should succeed
        assert all(result.status_code == HTTPStatus.OK for result in results)
        assert all(result.json()["id"] == asset_id for result in results)


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPIBusinessLogic:
    """Test business logic enforcement through the API layer."""

    def test_get_asset_titles_success(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        title_repository,
        title_content_repository,
    ) -> None:
        """Test retrieving titles associated with an asset."""
        from app.models.title_contents import ContentKind
        from app.schemas import TitleContentInsert
        from tests.factories import get_title_internal

        # Arrange - create an asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create titles and link them to the asset
        from tests.factories import TitleReadFactory

        title1 = TitleReadFactory()
        title2 = TitleReadFactory()
        created_title1 = title_repository.create(get_title_internal(title1))
        created_title2 = title_repository.create(get_title_internal(title2))

        # Link asset to titles
        title_content_repository.create_positioned(
            parent_title_id=created_title1.id,
            title_content=TitleContentInsert(kind=ContentKind.asset, asset_id=asset_id),
            position="start",
        )
        title_content_repository.create_positioned(
            parent_title_id=created_title2.id,
            title_content=TitleContentInsert(kind=ContentKind.asset, asset_id=asset_id),
            position="start",
        )

        # Act
        response = client.get(f"/api/assets/{asset_id}/titles")

        # Assert
        assert response.status_code == HTTPStatus.OK
        titles = response.json()
        assert isinstance(titles, list)
        assert len(titles) == 2
        assert all("parent_title" in item for item in titles)
        assert all("parent_title_id" in item for item in titles)
        parent_title_ids = {item["parent_title_id"] for item in titles}
        assert created_title1.id in parent_title_ids
        assert created_title2.id in parent_title_ids

    def test_get_asset_titles_empty_list(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving titles for an asset with no title associations."""
        # Arrange - create an asset with no title links
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Act
        response = client.get(f"/api/assets/{asset_id}/titles")

        # Assert
        assert response.status_code == HTTPStatus.OK
        titles = response.json()
        assert isinstance(titles, list)
        assert len(titles) == 0

    def test_get_asset_titles_not_found(self, client: TestClient) -> None:
        """Test retrieving titles for non-existent asset returns 404."""
        # Act
        response = client.get("/api/assets/99999/titles")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "detail" in response.json()
        assert "Asset not found" in response.json()["detail"]


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPIExternalIds:
    """Test GET /api/assets/by-scheme endpoint for retrieving assets by external ID."""

    def _create_scheme(
        self, client: TestClient, code: str, label: str, validator: str | None = None
    ) -> dict:
        """Helper to create an ID scheme."""
        payload = {"code": code, "label": label, "validator": validator}
        res = client.post("/api/id_schemes", json=payload)
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_get_asset_by_scheme_success(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving an asset by external ID through the by-scheme endpoint."""
        # Arrange - create asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create an ID scheme
        scheme = self._create_scheme(client, code="imdb", label="IMDb")
        scheme_id = scheme["id"]

        # Attach external ID to the asset
        ext_id_payload = {"scheme_id": scheme_id, "external_id": "tt1234567"}
        attach_response = client.post(f"/api/assets/{asset_id}/ids", json=ext_id_payload)
        assert attach_response.status_code == HTTPStatus.CREATED

        # Act - retrieve asset by external ID
        response = client.get(f"/api/assets/by-scheme/{scheme_id}/tt1234567")

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["id"] == asset_id
        assert r["path"] == created_asset.path
        assert r["filename"] == created_asset.filename

    def test_get_asset_by_scheme_not_found(self, client: TestClient) -> None:
        """Test that retrieving asset by non-existent external ID returns 404."""
        # Arrange - create a scheme but no asset with that external ID
        scheme = self._create_scheme(client, code="tvdb", label="TVDB")
        scheme_id = scheme["id"]

        # Act - try to retrieve non-existent asset
        response = client.get(f"/api/assets/by-scheme/{scheme_id}/nonexistent123")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "detail" in response.json()

    def test_get_asset_by_scheme_invalid_scheme_id(self, client: TestClient) -> None:
        """Test that retrieving asset with non-existent scheme ID returns 404."""
        # Act - try with non-existent scheme ID
        response = client.get("/api/assets/by-scheme/99999/ext123")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_get_asset_by_scheme_multiple_schemes(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving the same asset through different external ID schemes."""
        # Arrange - create asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create multiple ID schemes
        scheme1 = self._create_scheme(client, code="imdb", label="IMDb")
        scheme2 = self._create_scheme(client, code="tmdb", label="TMDB")

        # Attach multiple external IDs to the asset
        ext_id1 = {"scheme_id": scheme1["id"], "external_id": "tt7654321"}
        ext_id2 = {"scheme_id": scheme2["id"], "external_id": "98765"}

        r1 = client.post(f"/api/assets/{asset_id}/ids", json=ext_id1)
        r2 = client.post(f"/api/assets/{asset_id}/ids", json=ext_id2)
        assert r1.status_code == HTTPStatus.CREATED
        assert r2.status_code == HTTPStatus.CREATED

        # Act - retrieve asset through both schemes
        response1 = client.get(f"/api/assets/by-scheme/{scheme1['id']}/tt7654321")
        response2 = client.get(f"/api/assets/by-scheme/{scheme2['id']}/98765")

        # Assert - both should return the same asset
        assert response1.status_code == HTTPStatus.OK
        assert response2.status_code == HTTPStatus.OK
        assert response1.json()["id"] == asset_id
        assert response2.json()["id"] == asset_id

    def test_get_asset_by_scheme_case_sensitive_external_id(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test that external ID lookup is case-sensitive."""
        # Arrange - create asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create scheme and attach external ID with specific case
        scheme = self._create_scheme(client, code="test", label="Test")
        ext_id_payload = {"scheme_id": scheme["id"], "external_id": "AbCd123"}
        attach_response = client.post(f"/api/assets/{asset_id}/ids", json=ext_id_payload)
        assert attach_response.status_code == HTTPStatus.CREATED

        # Act - try with correct case and wrong case
        response_correct = client.get(f"/api/assets/by-scheme/{scheme['id']}/AbCd123")
        response_wrong = client.get(f"/api/assets/by-scheme/{scheme['id']}/abcd123")

        # Assert - correct case should work, wrong case should fail
        assert response_correct.status_code == HTTPStatus.OK
        assert response_correct.json()["id"] == asset_id
        assert response_wrong.status_code == HTTPStatus.NOT_FOUND

    def test_get_asset_by_scheme_special_characters(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test retrieving asset with external ID containing special characters."""
        # Arrange - create asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Create scheme with external ID containing special characters
        scheme = self._create_scheme(client, code="custom", label="Custom")
        ext_id_with_special = "ext-id_123.test"
        ext_id_payload = {"scheme_id": scheme["id"], "external_id": ext_id_with_special}
        attach_response = client.post(f"/api/assets/{asset_id}/ids", json=ext_id_payload)
        assert attach_response.status_code == HTTPStatus.CREATED

        # Act
        response = client.get(f"/api/assets/by-scheme/{scheme['id']}/{ext_id_with_special}")

        # Assert
        assert response.status_code == HTTPStatus.OK
        assert response.json()["id"] == asset_id

    def test_get_asset_by_scheme_different_assets_same_scheme(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test that different assets with different external IDs in the same scheme are correctly retrieved."""
        # Arrange - create two assets
        asset1 = AssetReadFactory()
        asset2 = AssetReadFactory()
        created_asset1 = media_repository.create(
            AssetCreateInternal(
                **asset1.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        created_asset2 = media_repository.create(
            AssetCreateInternal(
                **asset2.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Create one scheme and attach different external IDs to each asset
        scheme = self._create_scheme(client, code="shared", label="Shared Scheme")

        ext_id1 = {"scheme_id": scheme["id"], "external_id": "unique_id_1"}
        ext_id2 = {"scheme_id": scheme["id"], "external_id": "unique_id_2"}

        r1 = client.post(f"/api/assets/{created_asset1.id}/ids", json=ext_id1)
        r2 = client.post(f"/api/assets/{created_asset2.id}/ids", json=ext_id2)
        assert r1.status_code == HTTPStatus.CREATED
        assert r2.status_code == HTTPStatus.CREATED

        # Act - retrieve both assets
        response1 = client.get(f"/api/assets/by-scheme/{scheme['id']}/unique_id_1")
        response2 = client.get(f"/api/assets/by-scheme/{scheme['id']}/unique_id_2")

        # Assert - each should return the correct asset
        assert response1.status_code == HTTPStatus.OK
        assert response2.status_code == HTTPStatus.OK
        assert response1.json()["id"] == created_asset1.id
        assert response2.json()["id"] == created_asset2.id


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPIMetadata:
    def _create_asset(self, media_repository: MediaRepository) -> int:
        a = AssetReadFactory()
        created = media_repository.create(
            AssetCreateInternal(
                **a.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        return created.id

    def test_add_metadata_ffprobe_success(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        asset_id = self._create_asset(media_repository)
        payload = {
            "metadata_type": "ffprobe",
            "data": {"streams": 2, "format": {"name": "mp4"}},
        }
        r = client.post(f"/api/assets/{asset_id}/metadata", json=payload)
        assert r.status_code == HTTPStatus.CREATED
        body = r.json()
        assert body["asset_id"] == asset_id
        assert body["metadata_type"] == "ffprobe"
        assert isinstance(body.get("id"), int) and body["id"] > 0
        # verify it is listed
        li = client.get(f"/api/assets/{asset_id}/metadata")
        assert li.status_code == HTTPStatus.OK
        items = li.json()
        assert any(m["id"] == body["id"] for m in items)

    def test_add_metadata_null_type_should_fail(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        asset_id = self._create_asset(media_repository)
        r = client.post(
            f"/api/assets/{asset_id}/metadata",
            json={"metadata_type": None, "data": {}},
        )
        assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_add_metadata_nonexistent_asset_should_fail(self, client: TestClient) -> None:
        payload = {"metadata_type": "ffprobe", "data": {"ok": True}}
        r = client.post("/api/assets/999999/metadata", json=payload)
        assert r.status_code == HTTPStatus.NOT_FOUND

    def test_add_second_metadata_tvdb(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        asset_id = self._create_asset(media_repository)
        p1 = {"metadata_type": "tvdb", "data": {"series": "A", "id": 1}}
        p2 = {"metadata_type": "tvdb", "data": {"series": "B", "id": 2}}
        r1 = client.post(f"/api/assets/{asset_id}/metadata", json=p1)
        r2 = client.post(f"/api/assets/{asset_id}/metadata", json=p2)
        assert r1.status_code == HTTPStatus.CREATED
        assert r2.status_code == HTTPStatus.CREATED
        li = client.get(f"/api/assets/{asset_id}/metadata").json()
        tvdb_items = [m for m in li if m["metadata_type"] == "tvdb"]
        assert len(tvdb_items) >= 2  # at least two records exist

    def test_delete_metadata_item(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        asset_id = self._create_asset(media_repository)
        p = {"metadata_type": "ffprobe", "data": {"streams": 1}}
        created = client.post(f"/api/assets/{asset_id}/metadata", json=p).json()
        mid = created["id"]
        # delete
        d = client.delete(f"/api/assets/{asset_id}/metadata/{mid}")
        assert d.status_code in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)
        # verify it is gone
        li = client.get(f"/api/assets/{asset_id}/metadata").json()
        assert all(m["id"] != mid for m in li)

    def test_delete_metadata_different_asset_should_fail(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        a1 = self._create_asset(media_repository)
        a2 = self._create_asset(media_repository)
        created = client.post(
            f"/api/assets/{a1}/metadata", json={"metadata_type": "ffprobe", "data": {}}
        ).json()
        mid = created["id"]
        r = client.delete(f"/api/assets/{a2}/metadata/{mid}")
        assert r.status_code == HTTPStatus.NOT_FOUND

    def test_update_metadata_updates_timestamp(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        import time
        from datetime import datetime

        asset_id = self._create_asset(media_repository)
        created = client.post(
            f"/api/assets/{asset_id}/metadata",
            json={"metadata_type": "ffprobe", "data": {"a": 1}},
        ).json()
        mid = created["id"]
        before = datetime.fromisoformat(created["updated_at"])
        # ensure database clock can tick
        time.sleep(0.05)
        upd = client.patch(
            f"/api/assets/{asset_id}/metadata/{mid}",
            json={"data": {"a": 2, "b": 3}},
        )
        assert upd.status_code == HTTPStatus.OK
        body = upd.json()
        after = datetime.fromisoformat(body["updated_at"])
        assert after > before
        assert body["data"] == {"a": 2, "b": 3}

    def test_update_metadata_not_exist_should_fail(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        asset_id = self._create_asset(media_repository)
        r = client.patch(f"/api/assets/{asset_id}/metadata/999999", json={"data": {"x": 1}})
        assert r.status_code == HTTPStatus.NOT_FOUND

    def test_update_metadata_belongs_to_different_asset_should_fail(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        a1 = self._create_asset(media_repository)
        a2 = self._create_asset(media_repository)
        created = client.post(
            f"/api/assets/{a1}/metadata",
            json={"metadata_type": "tvdb", "data": {"s": 1}},
        ).json()
        mid = created["id"]
        r = client.patch(f"/api/assets/{a2}/metadata/{mid}", json={"data": {"s": 2}})
        assert r.status_code == HTTPStatus.NOT_FOUND

    def test_asset_filename_uniqueness_constraint(self, client: TestClient) -> None:
        """Test that duplicate filenames are handled appropriately."""
        # Arrange
        asset = AssetReadFactory()
        asset_data = get_asset_creation_json(asset)  # type: ignore

        # Act - create first asset
        response1 = client.post("/api/assets", json=asset_data)
        assert response1.status_code == HTTPStatus.CREATED

        # Try to create second asset with same path
        response2 = client.post(
            "/api/assets",
            json=get_asset_creation_json(
                AssetReadFactory(path=asset.path, filename=asset.filename)  # type: ignore
            ),
        )

        # Assert - should either succeed (if duplicates allowed) or fail with appropriate error
        # This depends on your business rules
        assert response2.status_code == HTTPStatus.CONFLICT

    def test_asset_parent_child_relationship_validation(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Test validation of parent-child asset relationships."""
        # Arrange - create a parent asset
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        # Act - try make asset child of a non-existent parent
        response = client.put(f"/api/assets/{asset_id + 1}/derived_assets/{asset_id}")

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND

        # Act - try to make child its own parent
        response = client.put(f"/api/assets/{asset_id}/derived_assets/{asset_id}")

        # Assert
        assert response.status_code == HTTPStatus.CONFLICT


@pytest.mark.api
@pytest.mark.integration
class TestAssetsAPIPerformRename:
    """Integration tests for the perform_rename feature on asset update."""

    def test_update_asset_with_perform_rename_success(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test updating asset path with perform_rename=true performs file rename."""
        media_root = Path(test_settings.media.media_root)

        # Create file in media root
        old_rel_path = "old_dir/old_file.mp4"
        old_abs_path = media_root / old_rel_path
        old_abs_path.parent.mkdir(parents=True, exist_ok=True)
        old_abs_path.write_text("test content")

        asset = AssetReadFactory(path=old_rel_path, filename="old_file.mp4")
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )
        asset_id = created_asset.id

        new_rel_path = "new_dir/new_file.mp4"
        new_abs_path = media_root / new_rel_path
        update_data = {
            "path": new_rel_path,
            "filename": "new_file.mp4",
        }

        # Act - update with perform_rename=true
        response = client.patch(f"/api/assets/{asset_id}?perform_rename=true", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["path"] == new_rel_path
        assert r["filename"] == "new_file.mp4"

        # Verify file was actually moved
        assert new_abs_path.exists()
        assert new_abs_path.read_text() == "test content"
        assert not old_abs_path.exists()

    def test_update_asset_with_perform_rename_auto_extracts_filename(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        media_root = Path(test_settings.media.media_root)

        old_rel_path = "old.mp4"
        old_abs_path = media_root / old_rel_path
        old_abs_path.write_text("content")

        asset = AssetReadFactory(path=old_rel_path, filename="old.mp4")
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        new_rel_path = "subdir/new.mp4"
        new_abs_path = media_root / new_rel_path
        update_data = {"path": new_rel_path}  # No filename provided

        # Act
        response = client.patch(
            f"/api/assets/{created_asset.id}?perform_rename=true", json=update_data
        )

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["filename"] == "new.mp4"  # Auto-extracted
        assert new_abs_path.exists()

    def test_update_asset_with_perform_rename_filename_mismatch(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        media_root = Path(test_settings.media.media_root)

        old_rel_path = "file.mp4"
        old_abs_path = media_root / old_rel_path
        old_abs_path.write_text("content")

        asset = AssetReadFactory(path=old_rel_path, filename="file.mp4")
        created_asset = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Act - filename doesn't match path
        new_rel_path = "newfile.mp4"
        update_data = {"path": new_rel_path, "filename": "wrongname.mp4"}
        response = client.patch(
            f"/api/assets/{created_asset.id}?perform_rename=true", json=update_data
        )

        # Assert
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "does not match" in response.json()["detail"][0]["msg"]
        assert old_abs_path.exists()  # File not moved

    def test_update_asset_with_perform_rename_path_exists_in_database(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that updating to a path used by another asset returns 409."""
        media_root = Path(test_settings.media.media_root)

        # Create two assets
        file1_rel_path = "file1.mp4"
        file2_rel_path = "file2.mp4"
        file1_abs_path = media_root / file1_rel_path
        file2_abs_path = media_root / file2_rel_path
        file1_abs_path.write_text("content1")
        file2_abs_path.write_text("content2")

        asset1 = AssetReadFactory(path=file1_rel_path, filename="file1.mp4")
        asset2 = AssetReadFactory(path=file2_rel_path, filename="file2.mp4")

        created1 = media_repository.create(
            AssetCreateInternal(**asset1.model_dump(exclude={"id", "created_at", "master_asset_id"}))  # type: ignore
        )
        created2 = media_repository.create(
            AssetCreateInternal(**asset2.model_dump(exclude={"id", "created_at", "master_asset_id"}))  # type: ignore
        )

        # Act - try to rename asset1 to asset2's path
        update_data = {"path": file2_rel_path, "filename": "file2.mp4"}
        response = client.patch(f"/api/assets/{created1.id}?perform_rename=true", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.CONFLICT
        assert "Another asset already exists" in response.json()["detail"]
        assert file1_abs_path.exists()  # Original file not moved

    def test_update_asset_with_perform_rename_target_file_exists(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that renaming to existing file path returns 409."""
        media_root = Path(test_settings.media.media_root)

        old_rel_path = "old.mp4"
        existing_rel_path = "existing.mp4"
        old_abs_path = media_root / old_rel_path
        existing_abs_path = media_root / existing_rel_path
        old_abs_path.write_text("old content")
        existing_abs_path.write_text("existing content")

        asset = AssetReadFactory(path=old_rel_path, filename="old.mp4")
        created = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))  # type: ignore
        )

        # Act - try to rename to existing file
        update_data = {"path": existing_rel_path, "filename": "existing.mp4"}
        response = client.patch(f"/api/assets/{created.id}?perform_rename=true", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.CONFLICT
        assert "File already exists" in response.json()["detail"]
        assert old_abs_path.exists()
        assert existing_abs_path.read_text() == "existing content"  # Not overwritten

    def test_update_asset_with_perform_rename_source_not_found(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that renaming non-existent source file returns 404."""
        media_root = Path(test_settings.media.media_root)

        # Asset exists in DB but file doesn't exist on disk
        nonexistent_rel_path = "nonexistent.mp4"
        asset = AssetReadFactory(path=nonexistent_rel_path, filename="nonexistent.mp4")
        created = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))  # type: ignore
        )

        # Act
        new_rel_path = "new.mp4"
        update_data = {"path": new_rel_path, "filename": "new.mp4"}
        response = client.patch(f"/api/assets/{created.id}?perform_rename=true", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "Source file not found" in response.json()["detail"]

    def test_update_asset_without_perform_rename_skips_file_operations(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that perform_rename=false (default) doesn't rename files."""
        media_root = Path(test_settings.media.media_root)

        old_rel_path = "old.mp4"
        old_abs_path = media_root / old_rel_path
        old_abs_path.write_text("content")

        asset = AssetReadFactory(path=old_rel_path, filename="old.mp4")
        created = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Act - update path without perform_rename
        new_rel_path = "new.mp4"
        new_abs_path = media_root / new_rel_path
        update_data = {"path": new_rel_path, "filename": "new.mp4"}
        response = client.patch(
            f"/api/assets/{created.id}", json=update_data  # No perform_rename parameter
        )

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["path"] == new_rel_path

        # File should NOT have been moved
        assert old_abs_path.exists()
        assert not new_abs_path.exists()

    def test_update_asset_perform_rename_false_explicitly(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that perform_rename=false explicitly skips file operations."""
        media_root = Path(test_settings.media.media_root)

        old_rel_path = "old.mp4"
        old_abs_path = media_root / old_rel_path
        old_abs_path.write_text("content")

        asset = AssetReadFactory(path=old_rel_path, filename="old.mp4")
        created = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Act
        new_rel_path = "new.mp4"
        new_abs_path = media_root / new_rel_path
        update_data = {"path": new_rel_path, "filename": "new.mp4"}
        response = client.patch(f"/api/assets/{created.id}?perform_rename=false", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.OK
        assert old_abs_path.exists()
        assert not new_abs_path.exists()

    def test_update_asset_perform_rename_with_subdirectories(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that perform_rename creates parent directories as needed."""
        media_root = Path(test_settings.media.media_root)

        old_rel_path = "old.mp4"
        old_abs_path = media_root / old_rel_path
        old_abs_path.write_text("content")

        asset = AssetReadFactory(path=old_rel_path, filename="old.mp4")
        created = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Act - move to deeply nested directory that doesn't exist
        new_rel_path = "level1/level2/level3/new.mp4"
        new_abs_path = media_root / new_rel_path
        update_data = {"path": new_rel_path, "filename": "new.mp4"}
        response = client.patch(f"/api/assets/{created.id}?perform_rename=true", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.OK
        assert new_abs_path.exists()
        assert new_abs_path.read_text() == "content"
        assert not old_abs_path.exists()

    def test_update_asset_perform_rename_only_updates_path(
        self, client: TestClient, media_repository: MediaRepository, test_settings: AppConfig
    ) -> None:
        """Test that perform_rename with non-path updates works normally."""
        media_root = Path(test_settings.media.media_root)

        file_rel_path = "file.mp4"
        file_abs_path = media_root / file_rel_path
        file_abs_path.write_text("content")

        asset = AssetReadFactory(path=file_rel_path, filename="file.mp4", bitrate=1000)
        created = media_repository.create(
            AssetCreateInternal(
                **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
            )
        )

        # Act - update bitrate only (no path change)
        update_data = {"bitrate": 2000}
        response = client.patch(f"/api/assets/{created.id}?perform_rename=true", json=update_data)

        # Assert
        assert response.status_code == HTTPStatus.OK
        r = response.json()
        assert r["bitrate"] == 2000
        assert r["path"] == file_rel_path  # Path unchanged
        assert file_abs_path.exists()  # File not moved


@contextmanager
def _statements_touching(engine: Engine, table: str) -> Iterator[list[str]]:
    """Collect every SQL statement issued against ``table`` inside the block.

    Args:
        engine: The engine the request's session is bound to.
        table: Table name to match, case-insensitively, against each statement.

    Yields:
        list[str]: The matching statements, appended as they are executed.
    """
    seen: list[str] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if table in statement.lower():
            seen.append(statement)

    event.listen(engine, "after_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine, "after_cursor_execute", _record)


@pytest.mark.api
@pytest.mark.integration
class TestAssetsListQueryCount:
    """Guard the cost of listing assets against the N+1 regression in #49.

    ``AssetReadExtended`` serialises ``external_ids`` unconditionally, so if the
    relationship ever goes back to a lazy load the endpoint issues one extra SELECT
    per row returned. That is invisible to a correctness test -- the response body is
    identical either way -- so it is asserted here as a query count instead.
    """

    def _create_scheme(self, client: TestClient) -> dict:
        payload = {"code": "imdb", "label": "IMDb", "validator": None}
        res = client.post("/api/id_schemes", json=payload)
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def _create_asset_with_external_id(self, client: TestClient, scheme_id: int, n: int) -> None:
        asset = AssetReadFactory()
        res = client.post("/api/assets", json=get_asset_creation_json(asset))  # type: ignore[arg-type]
        assert res.status_code == HTTPStatus.CREATED, res.text
        res = client.post(
            f"/api/assets/{res.json()['id']}/ids",
            json={"scheme_id": scheme_id, "external_id": f"tt{n:07d}"},
        )
        assert res.status_code == HTTPStatus.CREATED, res.text

    def test_external_id_queries_do_not_scale_with_rows_returned(
        self, client: TestClient, _test_engine: Engine
    ) -> None:
        """A larger page must not cost more queries against external_identifiers."""
        # Arrange - six assets, each carrying an external identifier to load
        scheme = self._create_scheme(client)
        for n in range(6):
            self._create_asset_with_external_id(client, scheme["id"], n)

        # Act - the same endpoint at two page sizes
        counts: dict[int, int] = {}
        for limit in (2, 6):
            with _statements_touching(_test_engine, "external_identifiers") as statements:
                response = client.get(f"/api/assets?limit={limit}")
            assert response.status_code == HTTPStatus.OK
            items = response.json()["items"]
            assert len(items) == limit
            # The field must actually be populated, or the count below proves nothing
            assert all(item["external_ids"] for item in items)
            counts[limit] = len(statements)

        # Assert - cost is a property of the request, not of the row count
        assert counts[2] == counts[6], (
            f"external_identifiers queries scaled with page size: "
            f"{counts[2]} for 2 rows, {counts[6]} for 6 rows"
        )
        assert counts[6] < 6, f"expected a bounded number of queries, got {counts[6]} for 6 rows"


@pytest.mark.api
@pytest.mark.integration
class TestAssetsPathPrefixFilter:
    """`?path_prefix=` behaviour, which had no test before #60.

    The filter was rewritten from `path ILIKE 'x%'` to `lower(path) LIKE 'x%'` so it
    can use ix_assets_path_lower -- ILIKE cannot use any index under this database's
    en_US.utf8 collation. The two forms are meant to be equivalent, and nothing
    asserted that, so these tests pin the behaviour rather than the implementation.
    """

    def _seed(self, media_repository: MediaRepository, *paths: str) -> None:
        for path in paths:
            asset = AssetReadFactory(path=path, filename=Path(path).name)
            media_repository.create(
                AssetCreateInternal(
                    **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
                )
            )

    def test_matches_only_paths_under_the_prefix(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        self._seed(
            media_repository,
            "shows/alpha/ep1.mkv",
            "shows/alpha/ep2.mkv",
            "shows/beta/ep1.mkv",
            "movies/alpha/film.mkv",
        )

        body = client.get("/api/assets?path_prefix=shows/alpha&limit=50").json()

        assert {item["path"] for item in body["items"]} == {
            "shows/alpha/ep1.mkv",
            "shows/alpha/ep2.mkv",
        }

    def test_is_case_insensitive(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """The property most at risk from the ILIKE rewrite."""
        self._seed(media_repository, "Shows/Alpha/ep1.mkv")

        body = client.get("/api/assets?path_prefix=shows/alpha&limit=50").json()

        assert [item["path"] for item in body["items"]] == ["Shows/Alpha/ep1.mkv"]

    def test_anchors_at_the_start_rather_than_matching_anywhere(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """A prefix filter must not behave like a substring one."""
        self._seed(media_repository, "archive/shows/alpha/ep1.mkv", "shows/alpha/ep2.mkv")

        body = client.get("/api/assets?path_prefix=shows/alpha&limit=50").json()

        assert [item["path"] for item in body["items"]] == ["shows/alpha/ep2.mkv"]

    def test_a_prefix_matching_nothing_is_an_empty_page(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        self._seed(media_repository, "shows/alpha/ep1.mkv")

        body = client.get("/api/assets?path_prefix=nothing/here&limit=50").json()

        assert body["items"] == []


@pytest.mark.api
@pytest.mark.integration
class TestAssetsSortKeys:
    """The set of supported sort keys, decided in #62.

    Six are supported and `mtime` is not: nothing sorted by it -- the front end
    offers the other six, the runner client sorts by id -- and it was the only key
    needing NULL sentinels. These tests pin the set, because "which sorts exist" is
    a contract a client reads once and relies on.
    """

    SUPPORTED = ["id", "created_at", "size", "filename", "path", "duration"]

    def _seed(self, media_repository: MediaRepository, count: int = 3) -> None:
        for i in range(count):
            asset = AssetReadFactory(path=f"sorted/clip{i}.mkv", filename=f"clip{i}.mkv")
            media_repository.create(
                AssetCreateInternal(
                    **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
                )
            )

    @pytest.mark.parametrize("key", SUPPORTED)
    def test_every_supported_sort_key_is_accepted(
        self, client: TestClient, media_repository: MediaRepository, key: str
    ) -> None:
        self._seed(media_repository)

        for direction in ("asc", "desc"):
            response = client.get(f"/api/assets?limit=10&sort={key}:{direction}")
            assert response.status_code == HTTPStatus.OK, f"{key}:{direction} rejected"
            assert len(response.json()["items"]) == 3

    def test_mtime_is_no_longer_a_sort_key(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Removed in #62: offered by the API, used by nothing.

        A caller asking for it now gets a validation error rather than a whole-table
        sort over a column with NULLs needing sentinel handling.
        """
        self._seed(media_repository)

        response = client.get("/api/assets?limit=10&sort=mtime:desc")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_an_unknown_sort_key_is_rejected_rather_than_ignored(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """Silently ignoring it would return the default order as if it were sorted."""
        self._seed(media_repository)

        response = client.get("/api/assets?limit=10&sort=nonsense:desc")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_the_default_sort_orders_by_created_at_descending(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """The front end's default, and the one this change indexes."""
        self._seed(media_repository, count=3)

        body = client.get("/api/assets?limit=10&sort=created_at:desc").json()
        stamps = [item["created_at"] for item in body["items"]]

        assert stamps == sorted(stamps, reverse=True)
