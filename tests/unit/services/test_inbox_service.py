"""Unit tests for InboxService."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, create_autospec

import pytest
from fastapi import HTTPException

from app.repositories import (
    InboxRepository,
    MediaRepository,
    TransformRequestRepository,
)
from app.repositories.errors import ForbiddenError, NotFoundError
from app.schemas import (
    AssetCreateInternal,
    InboxDeleteRequest,
    InboxImportRequest,
    TransformRequestCreateInternal,
)
from app.services import InboxService
from tests.factories import AssetReadFactory, TransformRequestReadFactory


class TestListInbox:
    """Tests for InboxService.list_inbox."""

    @pytest.mark.unit
    def test_list_inbox_success(self) -> None:
        """list_inbox returns list of inbox items."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        inbox_items = [object() for _ in range(3)]
        repo.list_all.return_value = inbox_items
        svc = InboxService(repo, m_repo, t_repo)

        result = svc.list_inbox()

        assert isinstance(result, list)
        assert len(result) == 3
        repo.list_all.assert_called_once()

    @pytest.mark.unit
    def test_list_inbox_empty_list(self) -> None:
        """list_inbox returns empty list when inbox is empty."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.list_all.return_value = []
        svc = InboxService(repo, m_repo, t_repo)

        result = svc.list_inbox()

        assert isinstance(result, list)
        assert len(result) == 0
        repo.list_all.assert_called_once()


class TestDelete:
    """Tests for InboxService.delete."""

    @pytest.mark.unit
    def test_delete_success(self) -> None:
        """delete removes inbox item successfully."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.delete.return_value = None
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxDeleteRequest(source="path/file.mp4")

        # Should not raise an exception
        svc.delete(req)

        repo.delete.assert_called_once_with(req)

    @pytest.mark.unit
    def test_delete_with_different_paths(self) -> None:
        """delete handles different source paths correctly."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        svc = InboxService(repo, m_repo, t_repo)

        test_paths = ["file1.mp4", "folder/file2.mkv", "deep/nested/path/file3.avi"]
        for source_path in test_paths:
            repo.reset_mock()
            repo.delete.return_value = None

            req = InboxDeleteRequest(source=source_path)
            svc.delete(req)

            repo.delete.assert_called_once_with(req)

    @pytest.mark.unit
    def test_delete_not_found(self) -> None:
        """delete raises 404 when inbox item doesn't exist."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.delete.side_effect = NotFoundError("missing")
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxDeleteRequest(source="path/file.mp4")

        with pytest.raises(HTTPException) as exc_info:
            svc.delete(req)

        assert exc_info.value.status_code == 404
        assert "Inbox item not found" in exc_info.value.detail
        repo.delete.assert_called_once_with(req)

    @pytest.mark.unit
    def test_delete_forbidden(self) -> None:
        """delete raises 403 when operation is forbidden."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.delete.side_effect = ForbiddenError("forbidden")
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxDeleteRequest(source="path/file.mp4")

        with pytest.raises(HTTPException) as exc_info:
            svc.delete(req)

        assert exc_info.value.status_code == 403
        assert "Forbidden" in exc_info.value.detail
        repo.delete.assert_called_once_with(req)

    @pytest.mark.unit
    def test_delete_unexpected_error(self) -> None:
        """delete raises 500 on unexpected errors."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.delete.side_effect = RuntimeError("boom")
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxDeleteRequest(source="path/file.mp4")

        with pytest.raises(HTTPException) as exc_info:
            svc.delete(req)

        assert exc_info.value.status_code == 500
        repo.delete.assert_called_once_with(req)


class TestImportFile:
    """Tests for InboxService.import_file."""

    @pytest.mark.unit
    def test_import_file_success(self) -> None:
        """import_file creates asset and transform requests successfully."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)

        req = InboxImportRequest(
            source="path/file.mp4", target="tvshows/Panorama/Season 1960/s1960e01.mp4"
        )

        # Mock file stat
        mock_stat = Mock()
        mock_stat.st_size = 1024
        mock_stat.st_mtime = 1234567890.0
        mock_path = Mock()
        mock_path.stat.return_value = mock_stat
        mock_path.name = "s1960e01.mp4"

        repo.move.return_value = (
            mock_path,
            Path("tvshows/Panorama/Season 1960/s1960e01.mp4"),
        )

        created_asset = AssetReadFactory(path=req.target, filename="s1960e01.mp4")
        m_repo.create.return_value = created_asset
        t_repo.create.return_value = TransformRequestReadFactory()
        svc = InboxService(repo, m_repo, t_repo)

        result = svc.import_file(req)

        assert result is created_asset
        repo.move.assert_called_once_with(req)

        # Verify asset creation with correct parameters
        m_repo.create.assert_called_once()
        asset_call_arg = m_repo.create.call_args[0][0]
        assert isinstance(asset_call_arg, AssetCreateInternal)
        assert asset_call_arg.path == "tvshows/Panorama/Season 1960/s1960e01.mp4"
        assert asset_call_arg.filename == "s1960e01.mp4"
        assert asset_call_arg.size == 1024
        assert asset_call_arg.mtime == datetime.fromtimestamp(1234567890.0, tz=UTC)

        # Verify two transform requests created
        assert t_repo.create.call_count == 2

        # Verify first transform request (stream_reader)
        first_transform_call = t_repo.create.call_args_list[0][0][0]
        assert isinstance(first_transform_call, TransformRequestCreateInternal)
        assert first_transform_call.transform_type == "prefect.stream_reader"
        assert first_transform_call.asset_id == created_asset.id

        # Verify second transform request (ffprobe_metadata)
        second_transform_call = t_repo.create.call_args_list[1][0][0]
        assert isinstance(second_transform_call, TransformRequestCreateInternal)
        assert second_transform_call.transform_type == "prefect.ffprobe_metadata"
        assert second_transform_call.asset_id == created_asset.id
        assert second_transform_call.parameters == {
            "schema_id": "probe@1",
            "categories": ["format", "chapters"],
        }

    @pytest.mark.unit
    def test_import_file_with_different_paths(self) -> None:
        """import_file handles various source and target paths."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        svc = InboxService(repo, m_repo, t_repo)

        test_cases = [
            ("source1.mp4", "target1.mp4"),
            ("inbox/nested/file.mkv", "library/shows/episode.mkv"),
            ("temp/video.avi", "archive/2024/video.avi"),
        ]

        for source, target in test_cases:
            repo.reset_mock()
            m_repo.reset_mock()
            t_repo.reset_mock()

            req = InboxImportRequest(source=source, target=target)

            mock_stat = Mock()
            mock_stat.st_size = 2048
            mock_stat.st_mtime = 1600000000.0
            mock_path = Mock()
            mock_path.stat.return_value = mock_stat
            mock_path.name = Path(target).name

            repo.move.return_value = (mock_path, Path(target))
            m_repo.create.return_value = AssetReadFactory(path=target, filename=Path(target).name)
            t_repo.create.return_value = TransformRequestReadFactory()

            result = svc.import_file(req)

            assert result.path == target
            repo.move.assert_called_once_with(req)

    @pytest.mark.unit
    def test_import_file_preserves_file_metadata(self) -> None:
        """import_file correctly captures file size and modification time."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)

        req = InboxImportRequest(source="source.mp4", target="target.mp4")

        # Test with specific file metadata
        mock_stat = Mock()
        mock_stat.st_size = 987654321
        mock_stat.st_mtime = 1700000000.5
        mock_path = Mock()
        mock_path.stat.return_value = mock_stat
        mock_path.name = "target.mp4"

        repo.move.return_value = (mock_path, Path("target.mp4"))
        m_repo.create.return_value = AssetReadFactory()
        t_repo.create.return_value = TransformRequestReadFactory()
        svc = InboxService(repo, m_repo, t_repo)

        svc.import_file(req)

        asset_call_arg = m_repo.create.call_args[0][0]
        assert asset_call_arg.size == 987654321
        assert asset_call_arg.mtime == datetime.fromtimestamp(1700000000.5, tz=UTC)

    @pytest.mark.unit
    def test_import_file_not_found(self) -> None:
        """import_file raises 404 when source file doesn't exist."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.move.side_effect = NotFoundError("missing")
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxImportRequest(
            source="path/file.mp4", target="tvshows/Panorama/Season 1960/s1960e01.mp4"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.import_file(req)

        assert exc_info.value.status_code == 404
        assert "Inbox item not found" in exc_info.value.detail
        repo.move.assert_called_once_with(req)
        m_repo.create.assert_not_called()
        t_repo.create.assert_not_called()

    @pytest.mark.unit
    def test_import_file_forbidden(self) -> None:
        """import_file raises 403 when operation is forbidden."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.move.side_effect = ForbiddenError("forbidden")
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxImportRequest(
            source="path/file.mp4", target="tvshows/Panorama/Season 1960/s1960e01.mp4"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.import_file(req)

        assert exc_info.value.status_code == 403
        assert "Forbidden" in exc_info.value.detail
        repo.move.assert_called_once_with(req)
        m_repo.create.assert_not_called()
        t_repo.create.assert_not_called()

    @pytest.mark.unit
    def test_import_file_unexpected_error(self) -> None:
        """import_file raises 500 on unexpected errors."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)
        repo.move.side_effect = RuntimeError("boom")
        svc = InboxService(repo, m_repo, t_repo)

        req = InboxImportRequest(
            source="path/file.mp4", target="tvshows/Panorama/Season 1960/s1960e01.mp4"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.import_file(req)

        assert exc_info.value.status_code == 500
        repo.move.assert_called_once_with(req)

    @pytest.mark.unit
    def test_import_file_creates_transform_requests_in_order(self) -> None:
        """import_file creates stream_reader transform request before ffprobe_metadata."""
        repo = create_autospec(InboxRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo = create_autospec(TransformRequestRepository, instance=True, spec_set=True)

        req = InboxImportRequest(source="source.mp4", target="target.mp4")

        mock_stat = Mock()
        mock_stat.st_size = 1024
        mock_stat.st_mtime = 1234567890.0
        mock_path = Mock()
        mock_path.stat.return_value = mock_stat
        mock_path.name = "target.mp4"

        repo.move.return_value = (mock_path, Path("target.mp4"))
        created_asset = AssetReadFactory(id=42, path="target.mp4", filename="target.mp4")
        m_repo.create.return_value = created_asset
        t_repo.create.return_value = TransformRequestReadFactory()
        svc = InboxService(repo, m_repo, t_repo)

        svc.import_file(req)

        # Verify order of transform request creation
        assert t_repo.create.call_count == 2
        calls = t_repo.create.call_args_list

        # First call should be stream_reader
        first_request = calls[0][0][0]
        assert first_request.transform_type == "prefect.stream_reader"
        assert first_request.asset_id == 42
        assert first_request.parameters == {}

        # Second call should be ffprobe_metadata
        second_request = calls[1][0][0]
        assert second_request.transform_type == "prefect.ffprobe_metadata"
        assert second_request.asset_id == 42
        assert second_request.parameters == {
            "schema_id": "probe@1",
            "categories": ["format", "chapters"],
        }
