"""
FastAPI Integration Tests for Streams API

Tests the complete request flow for stream management endpoints:
- Stream CRUD operations
- Stream metadata handling
- Asset-Stream relationships
- Stream filtering and querying
- Full-stack integration from API to database
"""

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import (
    MediaRepository,
    StreamRepository,
)
from app.schemas import (
    AssetCreateInternal,
    StreamCreateInternal,
)
from tests.factories import (
    AssetReadFactory,
    StreamReadFactory,
)


@pytest.mark.api
@pytest.mark.integration
class TestStreamsAPI:
    """Test the /streams API endpoints with full-stack integration."""

    def test_get_streams_empty_list(self, client: TestClient) -> None:
        """Test retrieving streams when none exist."""
        # Act
        response = client.get("/api/streams")

        # Assert
        assert response.status_code == 200
        streams = response.json()
        assert isinstance(streams, list)
        assert len(streams) == 0

    def test_get_streams_with_data(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """Test retrieving streams after creating some through repository."""
        # Arrange - create test data using factory
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        stream1 = StreamReadFactory(asset_id=asset_id)
        stream2 = StreamReadFactory(asset_id=asset_id)
        stream_repository.create(StreamCreateInternal(**stream1.model_dump(exclude={"id"})))
        stream_repository.create(StreamCreateInternal(**stream2.model_dump(exclude={"id"})))

        # Act
        response = client.get("/api/streams")

        # Assert
        assert response.status_code == 200
        streams = response.json()

        assert isinstance(streams, list)
        assert len(streams) == 2

        # Verify stream data is correctly serialized
        stream_indices = [stream["stream_index"] for stream in streams]
        assert stream1.stream_index in stream_indices
        assert stream2.stream_index in stream_indices

        # Verify all streams have correct asset reference
        assert all(stream["asset_id"] == asset_id for stream in streams)

    def test_get_stream_by_id_success(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test retrieving a specific stream by ID."""
        # Arrange - create asset and stream
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        stream = StreamReadFactory(asset_id=asset_id)
        created_stream = stream_repository.create(
            StreamCreateInternal(**stream.model_dump(exclude={"id"}))
        )
        stream_id = created_stream.id

        # Act
        response = client.get(f"/api/streams/{stream_id}")

        # Assert
        assert response.status_code == 200
        response_data = response.json()

        assert response_data["id"] == stream_id
        assert response_data["asset_id"] == asset_id
        assert response_data["stream_index"] == stream.stream_index
        assert response_data["codec_name"] == stream.codec_name
        assert response_data["codec_type"] == stream.codec_type
        assert response_data["width"] == stream.width
        assert response_data["height"] == stream.height
        assert response_data["frame_rate"] == stream.frame_rate

    def test_get_stream_by_id_not_found(self, client: TestClient):
        """Test retrieving non-existent stream returns 404."""
        # Act
        response = client.get("/api/streams/99999")

        # Assert
        assert response.status_code == 404
        assert "detail" in response.json()

    def test_update_stream_success(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test updating a stream through the API."""
        # Arrange - create asset and stream
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        stream = StreamReadFactory(asset_id=asset_id, frame_rate=24.9)
        created_stream = stream_repository.create(
            StreamCreateInternal(**stream.model_dump(exclude={"id"}))
        )
        stream_id = created_stream.id
        assert created_stream.frame_rate == 24.9

        # Prepare update data
        update_data = {
            "codec_name": "h265",
            "width": 1920,
            "height": 1080,
            "frame_rate": 60.0,
        }

        # Act
        response = client.patch(f"/api/streams/{stream_id}", json=update_data)

        # Assert
        assert response.status_code == 200
        response_data = response.json()

        assert response_data["id"] == stream_id
        assert response_data["codec_name"] == update_data["codec_name"]
        assert response_data["width"] == update_data["width"]
        assert response_data["height"] == update_data["height"]
        assert response_data["frame_rate"] == update_data["frame_rate"]

        # Verify unchanged fields remain the same
        assert response_data["asset_id"] == asset_id
        assert response_data["stream_index"] == stream.stream_index
        assert response_data["codec_type"] == stream.codec_type

    def test_update_stream_not_found(self, client: TestClient):
        """Test updating non-existent stream returns 404."""
        # Arrange
        update_data = {"codec_name": "h264"}

        # Act
        response = client.patch("/api/streams/99999", json=update_data)

        # Assert
        assert response.status_code == 404

    def test_update_stream_partial_data(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test updating stream with only some fields."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        stream = StreamReadFactory(asset_id=asset_id, codec_name="h264")
        created_stream = stream_repository.create(
            StreamCreateInternal(**stream.model_dump(exclude={"id"}))
        )
        stream_id = created_stream.id

        # Update only codec_name
        update_data = {"codec_name": "av1"}

        # Act
        response = client.patch(f"/api/streams/{stream_id}", json=update_data)

        # Assert
        assert response.status_code == 200
        response_data = response.json()

        # Verify updated field
        assert response_data["codec_name"] == "av1"

        # Verify other fields unchanged
        assert response_data["id"] == stream_id
        assert response_data["asset_id"] == asset_id
        assert response_data["stream_index"] == stream.stream_index

    def test_update_stream_validation_error(self, client: TestClient):
        """Test stream update with invalid data returns validation error."""
        # Arrange - invalid update data
        invalid_update_data = {
            "frame_rate": "30.a",  # Invalid type
            "width": -1920,  # Invalid negative width
        }

        # Act
        response = client.patch("/api/streams/1", json=invalid_update_data)

        # Assert
        assert response.status_code == 422
        error_detail = response.json()["detail"]
        # Verify validation errors mention the invalid fields
        assert any("frame_rate" in str(error).lower() for error in error_detail)


@pytest.mark.api
@pytest.mark.integration
class TestStreamsAPIFiltering:
    """Test stream filtering and querying capabilities."""

    def test_get_streams_by_asset_relationship(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test that streams are correctly associated with their assets."""
        # Arrange - create two assets with streams
        asset1 = AssetReadFactory()
        created_asset1 = media_repository.create(
            AssetCreateInternal(
                **asset1.model_dump(exclude={"id", "created_at", "master_asset_id"})
            )
        )
        asset1_id = created_asset1.id

        asset2 = AssetReadFactory()
        created_asset2 = media_repository.create(
            AssetCreateInternal(
                **asset2.model_dump(exclude={"id", "created_at", "master_asset_id"})
            )
        )
        asset2_id = created_asset2.id

        # Create streams for each asset
        stream1_asset1 = StreamReadFactory(asset_id=asset1_id, stream_index=0)
        stream2_asset1 = StreamReadFactory(asset_id=asset1_id, stream_index=1)
        stream1_asset2 = StreamReadFactory(asset_id=asset2_id, stream_index=0)

        stream_repository.create(StreamCreateInternal(**stream1_asset1.model_dump(exclude={"id"})))
        stream_repository.create(StreamCreateInternal(**stream2_asset1.model_dump(exclude={"id"})))
        stream_repository.create(StreamCreateInternal(**stream1_asset2.model_dump(exclude={"id"})))

        # Act
        response = client.get("/api/streams")

        # Assert
        assert response.status_code == 200
        streams = response.json()

        assert len(streams) == 3

        # Verify asset associations
        asset1_streams = [s for s in streams if s["asset_id"] == asset1_id]
        asset2_streams = [s for s in streams if s["asset_id"] == asset2_id]

        assert len(asset1_streams) == 2
        assert len(asset2_streams) == 1

        # Verify stream indices are preserved
        asset1_indices = {s["stream_index"] for s in asset1_streams}
        assert asset1_indices == {0, 1}

    def test_get_streams_codec_types(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test streams with different codec types."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        # Create streams with different codec types
        video_stream = StreamReadFactory(
            asset_id=asset_id,
            stream_index=0,
            codec_type="video",
            codec_name="h264",
            width=1920,
            height=1080,
        )
        audio_stream = StreamReadFactory(
            asset_id=asset_id,
            stream_index=1,
            codec_type="audio",
            codec_name="aac",
            width=None,
            height=None,
        )
        subtitle_stream = StreamReadFactory(
            asset_id=asset_id,
            stream_index=2,
            codec_type="subtitle",
            codec_name="srt",
            width=None,
            height=None,
        )

        stream_repository.create(StreamCreateInternal(**video_stream.model_dump(exclude={"id"})))
        stream_repository.create(StreamCreateInternal(**audio_stream.model_dump(exclude={"id"})))
        stream_repository.create(StreamCreateInternal(**subtitle_stream.model_dump(exclude={"id"})))

        # Act
        response = client.get("/api/streams")

        # Assert
        assert response.status_code == 200
        streams = response.json()

        assert len(streams) == 3

        # Group streams by codec type
        streams_by_type = {}
        for stream in streams:
            codec_type = stream["codec_type"]
            if codec_type not in streams_by_type:
                streams_by_type[codec_type] = []
            streams_by_type[codec_type].append(stream)

        # Verify each codec type has correct properties
        assert len(streams_by_type["video"]) == 1
        video = streams_by_type["video"][0]
        assert video["codec_name"] == "h264"
        assert video["width"] == 1920
        assert video["height"] == 1080

        assert len(streams_by_type["audio"]) == 1
        audio = streams_by_type["audio"][0]
        assert audio["codec_name"] == "aac"
        assert audio["width"] is None
        assert audio["height"] is None

        assert len(streams_by_type["subtitle"]) == 1
        subtitle = streams_by_type["subtitle"][0]
        assert subtitle["codec_name"] == "srt"


@pytest.mark.api
@pytest.mark.integration
class TestStreamsAPIErrorHandling:
    """Test error cases and edge conditions for the Streams API."""

    def test_invalid_stream_id_types(self, client: TestClient):
        """Test that invalid stream ID types return appropriate errors."""
        invalid_ids = ["abc", "12.5", "null"]

        for invalid_id in invalid_ids:
            response = client.get(f"/api/streams/{invalid_id}")
            # Should return 422 (validation error) or 404 depending on FastAPI version
            assert response.status_code in [404, 422]

    def test_malformed_json_in_update(self, client: TestClient):
        """Test handling of malformed JSON in PATCH requests."""
        # Test malformed JSON
        response = client.patch(
            "/api/streams/1",
            content='{"codec_name": "h264", invalid json}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422

    def test_update_with_empty_body(self, client: TestClient):
        """Test updating stream with empty request body."""
        # Act
        response = client.patch("/api/streams/1", json={})

        # Assert - should either succeed (no-op) or return validation error
        assert response.status_code in [200, 404, 422]


@pytest.mark.api
@pytest.mark.integration
class TestStreamsAPIBusinessLogic:
    """Test business logic enforcement through the Streams API."""

    def test_stream_asset_relationship_integrity(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test that streams maintain proper relationship with assets."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        stream = StreamReadFactory(asset_id=asset_id)
        created_stream = stream_repository.create(
            StreamCreateInternal(**stream.model_dump(exclude={"id"}))
        )
        stream_id = created_stream.id

        # Act - try to update stream (asset relationship should remain intact)
        update_data = {"codec_name": "av1"}
        response = client.patch(f"/api/streams/{stream_id}", json=update_data)

        # Assert
        assert response.status_code == 200
        response_data = response.json()

        # Verify asset relationship is preserved
        assert response_data["asset_id"] == asset_id

        # Verify the updated codec
        assert response_data["codec_name"] == "av1"

    def test_video_stream_dimension_constraints(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test that video stream dimensions are handled correctly."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        video_stream = StreamReadFactory(
            asset_id=asset_id,
            codec_type="video",
            width=1280,
            height=720,
        )
        created_stream = stream_repository.create(
            StreamCreateInternal(**video_stream.model_dump(exclude={"id"}))
        )
        stream_id = created_stream.id

        # Act - update video dimensions
        update_data = {
            "width": 1920,
            "height": 1080,
        }
        response = client.patch(f"/api/streams/{stream_id}", json=update_data)

        # Assert
        assert response.status_code == 200
        response_data = response.json()

        assert response_data["width"] == 1920
        assert response_data["height"] == 1080
        assert response_data["codec_type"] == "video"

    def test_audio_stream_no_dimensions(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test that audio streams handle null dimensions correctly."""
        # Arrange
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        audio_stream = StreamReadFactory(
            asset_id=asset_id,
            codec_type="audio",
            width=None,
            height=None,
        )
        created_stream = stream_repository.create(
            StreamCreateInternal(**audio_stream.model_dump(exclude={"id"}))
        )
        stream_id = created_stream.id

        # Act - try to update audio stream
        update_data = {"codec_name": "opus"}
        response = client.patch(f"/api/streams/{stream_id}", json=update_data)

        # Assert
        assert response.status_code == 200
        response_data = response.json()

        assert response_data["codec_name"] == "opus"
        assert response_data["codec_type"] == "audio"
        assert response_data["width"] is None
        assert response_data["height"] is None


@pytest.mark.api
@pytest.mark.integration
class TestStreamsAPIPerformance:
    """Performance tests for the Streams API."""

    def test_get_many_streams_performance(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ):
        """Test retrieving many streams performs acceptably."""
        # Arrange - create asset with many streams
        asset = AssetReadFactory()
        created_asset = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        asset_id = created_asset.id

        # Create 50 streams
        for i in range(50):
            stream = StreamReadFactory(asset_id=asset_id, stream_index=i)
            stream_repository.create(StreamCreateInternal(**stream.model_dump(exclude={"id"})))

        # Act
        import time

        start_time = time.time()
        response = client.get("/api/streams")
        end_time = time.time()

        # Assert
        assert response.status_code == 200
        streams = response.json()
        assert len(streams) == 50

        # Performance assertion (adjust threshold as needed)
        duration = end_time - start_time
        assert duration < 5.0, f"Query took {duration}s, expected < 5s"
