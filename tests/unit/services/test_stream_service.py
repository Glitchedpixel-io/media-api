"""Unit tests for StreamService."""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.repositories.protocols import MediaRepository, StreamRepository
from app.schemas import StreamCreateInternal, StreamCreatePublic, StreamPatchPublic
from app.services import StreamService
from tests.factories import AssetReadFactory, StreamReadFactory


class TestGetStream:
    """Tests for StreamService.get_stream."""

    @pytest.mark.unit
    def test_get_stream_success(self) -> None:
        """get_stream returns stream when found."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        expected_stream = StreamReadFactory(id=42, codec_type="video")
        s_repo.get.return_value = expected_stream
        svc = StreamService(s_repo, m_repo)

        result = svc.get_stream(42)

        assert result is expected_stream
        assert result.id == 42
        s_repo.get.assert_called_once_with(42)

    @pytest.mark.unit
    def test_get_stream_not_found(self) -> None:
        """get_stream raises 404 when stream doesn't exist."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.get.return_value = None
        svc = StreamService(s_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_stream(123)

        assert exc_info.value.status_code == 404
        assert "Stream not found" in exc_info.value.detail
        s_repo.get.assert_called_once_with(123)

    @pytest.mark.unit
    def test_get_stream_with_different_ids(self) -> None:
        """get_stream correctly handles different stream IDs."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        svc = StreamService(s_repo, m_repo)

        test_ids = [1, 50, 999]
        for stream_id in test_ids:
            s_repo.reset_mock()
            s_repo.get.return_value = StreamReadFactory(id=stream_id)

            result = svc.get_stream(stream_id)

            assert result.id == stream_id
            s_repo.get.assert_called_once_with(stream_id)


class TestGetStreams:
    """Tests for StreamService.get_streams."""

    @pytest.mark.unit
    def test_get_streams_success(self) -> None:
        """get_streams returns list of all streams."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        streams = [StreamReadFactory() for _ in range(5)]
        s_repo.list_all.return_value = streams
        svc = StreamService(s_repo, m_repo)

        result = svc.get_streams()

        assert isinstance(result, list)
        assert len(result) == 5
        s_repo.list_all.assert_called_once()

    @pytest.mark.unit
    def test_get_streams_empty_list(self) -> None:
        """get_streams returns empty list when no streams exist."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.list_all.return_value = []
        svc = StreamService(s_repo, m_repo)

        result = svc.get_streams()

        assert isinstance(result, list)
        assert len(result) == 0
        s_repo.list_all.assert_called_once()


class TestCreateStream:
    """Tests for StreamService.create_stream."""

    @pytest.mark.unit
    def test_create_stream_success(self) -> None:
        """create_stream creates new stream and returns it."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        created_stream = StreamReadFactory(id=1, asset_id=5, codec_type="audio")
        s_repo.create.return_value = created_stream
        svc = StreamService(s_repo, m_repo)

        payload = StreamCreatePublic(codec_type="audio")

        result = svc.create_stream(5, payload)

        assert result is created_stream
        assert result.asset_id == 5
        assert result.codec_type == "audio"

        # Verify internal DTO conversion
        s_repo.create.assert_called_once()
        call_arg = s_repo.create.call_args[0][0]
        assert isinstance(call_arg, StreamCreateInternal)
        assert call_arg.asset_id == 5
        assert call_arg.codec_type == "audio"

    @pytest.mark.unit
    def test_create_stream_with_all_fields(self) -> None:
        """create_stream handles all stream fields correctly."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        created_stream = StreamReadFactory(
            id=1,
            asset_id=10,
            codec_type="video",
            codec_name="h264",
            stream_index=0,
            language="eng",
            width=1920,
            height=1080,
            frame_rate=30.0,
        )
        s_repo.create.return_value = created_stream
        svc = StreamService(s_repo, m_repo)

        payload = StreamCreatePublic(
            codec_type="video",
            codec_name="h264",
            stream_index=0,
            language="eng",
            width=1920,
            height=1080,
            frame_rate=30.0,
        )

        result = svc.create_stream(10, payload)

        assert result.codec_name == "h264"
        assert result.width == 1920
        assert result.height == 1080

    @pytest.mark.unit
    def test_create_stream_unique_violation(self) -> None:
        """create_stream raises 409 on unique constraint violation."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.create.side_effect = UniqueViolation("u")
        svc = StreamService(s_repo, m_repo)

        payload = StreamCreatePublic(codec_type="audio")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_stream(5, payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_stream_database_locked(self) -> None:
        """create_stream raises 423 when database is read-only."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.create.side_effect = DatabaseLocked("locked")
        svc = StreamService(s_repo, m_repo)

        payload = StreamCreatePublic(codec_type="video")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_stream(7, payload)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_create_stream_constraint_violations(self, exc_class) -> None:
        """create_stream raises 422 for various constraint violations."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.create.side_effect = exc_class("c")
        svc = StreamService(s_repo, m_repo)

        payload = StreamCreatePublic(codec_type="video")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_stream(7, payload)

        assert exc_info.value.status_code == 422


class TestUpdateStream:
    """Tests for StreamService.update_stream."""

    @pytest.mark.unit
    def test_update_stream_success_with_exclude_none(self) -> None:
        """update_stream updates stream with exclude_none=True."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        updated_stream = StreamReadFactory(id=9, codec_name="aac")
        s_repo.update.return_value = updated_stream
        svc = StreamService(s_repo, m_repo)

        patch = StreamPatchPublic(codec_name="aac")

        result = svc.update_stream(9, patch, exclude_none=True)

        assert result is updated_stream
        assert result.codec_name == "aac"
        s_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_stream_success_without_exclude_none(self) -> None:
        """update_stream updates stream with exclude_none=False."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        updated_stream = StreamReadFactory(id=9)
        s_repo.update.return_value = updated_stream
        svc = StreamService(s_repo, m_repo)

        patch = StreamPatchPublic(codec_name="h264")

        result = svc.update_stream(9, patch, exclude_none=False)

        assert result is updated_stream
        s_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_stream_partial_update(self) -> None:
        """update_stream allows partial field updates."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.update.return_value = StreamReadFactory()
        svc = StreamService(s_repo, m_repo)

        # Only update codec_name
        patch = StreamPatchPublic(codec_name="vp9")

        svc.update_stream(9, patch)

        s_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_stream_not_found(self) -> None:
        """update_stream raises 404 when stream doesn't exist."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.update.side_effect = NotFoundError("missing")
        svc = StreamService(s_repo, m_repo)

        patch = StreamPatchPublic(codec_name="aac")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_stream(9, patch)

        assert exc_info.value.status_code == 404
        assert "Stream not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_stream_unique_violation(self) -> None:
        """update_stream raises 409 on unique constraint violation."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.update.side_effect = UniqueViolation("u")
        svc = StreamService(s_repo, m_repo)

        patch = StreamPatchPublic(codec_name="aac")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_stream(9, patch)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_stream_database_locked(self) -> None:
        """update_stream raises 423 when database is read-only."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.update.side_effect = DatabaseLocked("locked")
        svc = StreamService(s_repo, m_repo)

        patch = StreamPatchPublic(codec_name="h264")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_stream(9, patch)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [
            ForeignKeyViolation,
            NotNullViolation,
            CheckViolation,
            EnumViolation,
            ConstraintViolation,
        ],
    )
    def test_update_stream_constraint_violations(self, exc_class) -> None:
        """update_stream raises 422 for various constraint violations."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        s_repo.update.side_effect = exc_class("c")
        svc = StreamService(s_repo, m_repo)

        patch = StreamPatchPublic(codec_name="h264")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_stream(9, patch)

        assert exc_info.value.status_code == 422


class TestGetAssetStreams:
    """Tests for StreamService.get_asset_streams."""

    @pytest.mark.unit
    def test_get_asset_streams_success(self) -> None:
        """get_asset_streams returns list of streams for asset."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.get.return_value = AssetReadFactory(id=11)
        streams = [StreamReadFactory(asset_id=11) for _ in range(3)]
        s_repo.get_asset_streams.return_value = streams
        svc = StreamService(s_repo, m_repo)

        result = svc.get_asset_streams(11)

        assert isinstance(result, list)
        assert len(result) == 3
        m_repo.get.assert_called_once_with(11)
        s_repo.get_asset_streams.assert_called_once_with(11)

    @pytest.mark.unit
    def test_get_asset_streams_empty_list(self) -> None:
        """get_asset_streams returns empty list when asset has no streams."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.get.return_value = AssetReadFactory(id=11)
        s_repo.get_asset_streams.return_value = []
        svc = StreamService(s_repo, m_repo)

        result = svc.get_asset_streams(11)

        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.unit
    def test_get_asset_streams_asset_not_found(self) -> None:
        """get_asset_streams raises 404 when asset doesn't exist."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.get.return_value = None
        svc = StreamService(s_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_streams(11)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.get.assert_called_once_with(11)
        s_repo.get_asset_streams.assert_not_called()

    @pytest.mark.unit
    def test_get_asset_streams_with_different_asset_ids(self) -> None:
        """get_asset_streams correctly handles different asset IDs."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        svc = StreamService(s_repo, m_repo)

        test_ids = [1, 50, 999]
        for asset_id in test_ids:
            s_repo.reset_mock()
            m_repo.reset_mock()
            m_repo.get.return_value = AssetReadFactory(id=asset_id)
            s_repo.get_asset_streams.return_value = []

            svc.get_asset_streams(asset_id)

            m_repo.get.assert_called_once_with(asset_id)
            s_repo.get_asset_streams.assert_called_once_with(asset_id)


class TestDeleteAssetStreams:
    """Tests for StreamService.delete_asset_streams."""

    @pytest.mark.unit
    def test_delete_asset_streams_success(self) -> None:
        """delete_asset_streams deletes all streams for asset."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        svc = StreamService(s_repo, m_repo)

        # Should not raise an exception
        svc.delete_asset_streams(22)

        m_repo.exists.assert_called_once_with(22)
        s_repo.delete_asset_streams.assert_called_once_with(22)

    @pytest.mark.unit
    def test_delete_asset_streams_asset_not_found(self) -> None:
        """delete_asset_streams raises 404 when asset doesn't exist."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = StreamService(s_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_asset_streams(22)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.exists.assert_called_once_with(22)
        s_repo.delete_asset_streams.assert_not_called()

    @pytest.mark.unit
    def test_delete_asset_streams_database_locked(self) -> None:
        """delete_asset_streams raises 423 when database is read-only."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        s_repo.delete_asset_streams.side_effect = DatabaseLocked("locked")
        svc = StreamService(s_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_asset_streams(22)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    def test_delete_asset_streams_with_different_asset_ids(self) -> None:
        """delete_asset_streams correctly handles different asset IDs."""
        s_repo = create_autospec(StreamRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        svc = StreamService(s_repo, m_repo)

        test_ids = [1, 50, 999]
        for asset_id in test_ids:
            s_repo.reset_mock()
            m_repo.reset_mock()
            m_repo.exists.return_value = True

            svc.delete_asset_streams(asset_id)

            m_repo.exists.assert_called_once_with(asset_id)
            s_repo.delete_asset_streams.assert_called_once_with(asset_id)
