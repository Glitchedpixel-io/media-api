"""Unit tests for MediaService."""

from __future__ import annotations

from unittest.mock import create_autospec, patch

import pytest
from fastapi import HTTPException

from app.config import AppConfig
from app.repositories import MediaRepository
from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    DuplicatePathError,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.schemas import (
    AssetCreateInternal,
    AssetCreatePublic,
    AssetListParams,
    AssetPatchPublic,
    AssetUpdateInternal,
    PageInfo,
    PaginatedResponse,
)
from app.services import MediaService
from tests.factories import AssetReadFactory, AssetReadExtendedFactory


@pytest.fixture
def repo() -> MediaRepository:
    return create_autospec(MediaRepository, instance=True, spec_set=True)


@pytest.fixture
def svc(repo, test_settings: AppConfig) -> MediaService:
    return MediaService(repo, test_settings.media)


class TestGetAsset:
    """Tests for MediaService.get_asset."""

    @pytest.mark.unit
    def test_get_asset_success(self, repo, svc) -> None:
        """get_asset returns asset when found in repository."""
        expected_asset = AssetReadFactory(id=42, filename="test.mp4")
        repo.get.return_value = expected_asset

        result = svc.get_asset(42)

        assert result is expected_asset
        assert result.id == 42
        assert result.filename == "test.mp4"
        repo.get.assert_called_once_with(42)

    @pytest.mark.unit
    def test_get_asset_not_found(self, repo, svc) -> None:
        """get_asset raises 404 when repository returns None."""
        repo.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset(999)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        repo.get.assert_called_once_with(999)

    @pytest.mark.unit
    def test_get_asset_with_various_ids(self, repo, svc) -> None:
        """get_asset correctly handles different asset IDs."""

        test_ids = [1, 100, 999999]
        for asset_id in test_ids:
            repo.reset_mock()
            expected = AssetReadFactory(id=asset_id)
            repo.get.return_value = expected

            result = svc.get_asset(asset_id)

            assert result.id == asset_id
            repo.get.assert_called_once_with(asset_id)


class TestGetAssetByExternalId:
    """Tests for MediaService.get_asset_by_external_id."""

    @pytest.mark.unit
    def test_get_asset_by_external_id_success(self, repo, svc) -> None:
        """get_asset_by_external_id returns asset when found."""
        expected_asset = AssetReadFactory(id=10)
        repo.get_by_external_id.return_value = expected_asset

        result = svc.get_asset_by_external_id(scheme_id=1, external_id="ext123")

        assert result is expected_asset
        assert result.id == 10
        repo.get_by_external_id.assert_called_once_with(1, "ext123")

    @pytest.mark.unit
    def test_get_asset_by_external_id_not_found(self, repo, svc) -> None:
        """get_asset_by_external_id raises 404 when not found."""
        repo.get_by_external_id.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_by_external_id(scheme_id=1, external_id="unknown")

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        repo.get_by_external_id.assert_called_once_with(1, "unknown")


class TestGetDerivedAssets:
    """Tests for MediaService.get_derived_assets."""

    @pytest.mark.unit
    def test_get_derived_assets_success(self, repo, svc) -> None:
        """get_derived_assets returns list of derived assets."""
        master_asset = AssetReadFactory(id=5)
        derived_assets = [AssetReadFactory(id=10), AssetReadFactory(id=11)]
        repo.get.return_value = master_asset
        repo.list_derived_assets.return_value = derived_assets

        result = svc.get_derived_assets(5)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0].id == 10
        assert result[1].id == 11
        repo.get.assert_called_once_with(5)
        repo.list_derived_assets.assert_called_once_with(5)

    @pytest.mark.unit
    def test_get_derived_assets_empty_list(self, repo, svc) -> None:
        """get_derived_assets returns empty list when no derived assets exist."""
        repo.get.return_value = AssetReadFactory(id=5)
        repo.list_derived_assets.return_value = []

        result = svc.get_derived_assets(5)

        assert isinstance(result, list)
        assert len(result) == 0
        repo.list_derived_assets.assert_called_once_with(5)

    @pytest.mark.unit
    def test_get_derived_assets_master_not_found(self, repo, svc) -> None:
        """get_derived_assets raises 404 when master asset doesn't exist."""
        repo.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            svc.get_derived_assets(999)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        repo.get.assert_called_once_with(999)
        repo.list_derived_assets.assert_not_called()


class TestAddDerivedAsset:
    """Tests for MediaService.add_derived_asset."""

    @pytest.mark.unit
    def test_add_derived_asset_success(self, repo, svc) -> None:
        """add_derived_asset creates parent-child relationship successfully."""
        repo.exists.return_value = True
        updated_child = AssetReadFactory(id=15, master_asset_id=10)
        repo.update.return_value = updated_child

        result = svc.add_derived_asset(asset_id=10, child_asset_id=15)

        assert result is updated_child
        assert result.id == 15
        assert result.master_asset_id == 10
        repo.exists.assert_any_call(10)
        repo.exists.assert_any_call(15)
        assert repo.exists.call_count == 2

        # Verify internal DTO structure
        call_args = repo.update.call_args[0]
        assert call_args[0] == 15
        assert isinstance(call_args[1], AssetUpdateInternal)
        assert call_args[1].master_asset_id == 10

    @pytest.mark.unit
    def test_add_derived_asset_master_not_found(self, repo, svc) -> None:
        """add_derived_asset raises 404 when master asset doesn't exist."""

        repo.exists.side_effect = lambda asset_id: asset_id != 999

        with pytest.raises(HTTPException) as exc_info:
            svc.add_derived_asset(asset_id=999, child_asset_id=15)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        repo.update.assert_not_called()

    @pytest.mark.unit
    def test_add_derived_asset_child_not_found(self, repo, svc) -> None:
        """add_derived_asset raises 404 when child asset doesn't exist."""

        repo.exists.side_effect = lambda asset_id: asset_id != 888

        with pytest.raises(HTTPException) as exc_info:
            svc.add_derived_asset(asset_id=10, child_asset_id=888)

        assert exc_info.value.status_code == 404
        repo.update.assert_not_called()

    @pytest.mark.unit
    def test_add_derived_asset_check_violation(self, repo, svc) -> None:
        """add_derived_asset raises 409 when relationship violates business rules."""

        repo.exists.return_value = True
        repo.update.side_effect = CheckViolation("Circular relationship detected")

        with pytest.raises(HTTPException) as exc_info:
            svc.add_derived_asset(asset_id=10, child_asset_id=15)

        assert exc_info.value.status_code == 409
        assert "Relationship not permitted" in exc_info.value.detail

    @pytest.mark.unit
    def test_add_derived_asset_unique_violation(self, repo, svc) -> None:
        """add_derived_asset raises 409 on unique constraint violation."""

        repo.exists.return_value = True
        repo.update.side_effect = UniqueViolation("Unique constraint violated")

        with pytest.raises(HTTPException) as exc_info:
            svc.add_derived_asset(asset_id=10, child_asset_id=15)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_add_derived_asset_database_locked(self, repo, svc) -> None:
        """add_derived_asset raises 423 when database is read-only."""

        repo.exists.return_value = True
        repo.update.side_effect = DatabaseLocked("Database locked")

        with pytest.raises(HTTPException) as exc_info:
            svc.add_derived_asset(asset_id=10, child_asset_id=15)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "exc_class",
        [ForeignKeyViolation, NotNullViolation, EnumViolation, ConstraintViolation],
    )
    def test_add_derived_asset_constraint_violations(self, exc_class, repo, svc) -> None:
        """add_derived_asset raises 422 for various constraint violations."""

        repo.exists.return_value = True
        repo.update.side_effect = exc_class("Constraint error")

        with pytest.raises(HTTPException) as exc_info:
            svc.add_derived_asset(asset_id=10, child_asset_id=15)

        assert exc_info.value.status_code == 422


class TestGetAssets:
    """Tests for MediaService.get_assets."""

    @pytest.mark.unit
    def test_get_assets_with_default_params(self, repo, svc) -> None:
        """get_assets delegates to repository with provided params."""

        assets = [AssetReadFactory() for _ in range(3)]
        expected_response = PaginatedResponse(items=assets, page=PageInfo(next=None, prev=None))
        repo.list_paged.return_value = expected_response

        params = AssetListParams()

        result = svc.get_assets(params)

        assert result is expected_response
        assert len(result.items) == 3
        assert result.page.next is None
        repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_assets_with_pagination(self, repo, svc) -> None:
        """get_assets passes pagination parameters correctly."""

        repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next="next_cursor", prev="prev_cursor")
        )

        params = AssetListParams(limit=25, after="cursor123", sort="filename:asc")

        result = svc.get_assets(params)

        assert result.page.next == "next_cursor"
        assert result.page.prev == "prev_cursor"
        repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_assets_empty_result(self, repo, svc) -> None:
        """get_assets returns empty list when no assets match."""

        empty_response = PaginatedResponse(items=[], page=PageInfo(next=None, prev=None))
        repo.list_paged.return_value = empty_response

        params = AssetListParams()

        result = svc.get_assets(params)

        assert len(result.items) == 0
        assert isinstance(result.items, list)


class TestMarkAssetsSeen:
    """Tests for MediaService.mark_assets_seen."""

    @pytest.mark.unit
    def test_mark_assets_seen_success(self, repo, svc) -> None:
        """mark_assets_seen updates assets and returns count."""

        repo.mark_assets_seen.return_value = 3

        count = svc.mark_assets_seen([1, 2, 3])

        assert count == 3
        repo.mark_assets_seen.assert_called_once_with([1, 2, 3])

    @pytest.mark.unit
    def test_mark_assets_seen_empty_list(self, repo, svc) -> None:
        """mark_assets_seen returns 0 and doesn't call repo for empty list."""

        count = svc.mark_assets_seen([])

        assert count == 0
        repo.mark_assets_seen.assert_not_called()

    @pytest.mark.unit
    def test_mark_assets_seen_single_asset(self, repo, svc) -> None:
        """mark_assets_seen works with single asset ID."""

        repo.mark_assets_seen.return_value = 1

        count = svc.mark_assets_seen([42])

        assert count == 1
        repo.mark_assets_seen.assert_called_once_with([42])

    @pytest.mark.unit
    def test_mark_assets_seen_database_locked(self, repo, svc) -> None:
        """mark_assets_seen raises 423 when database is read-only."""

        repo.mark_assets_seen.side_effect = DatabaseLocked("Database locked")

        with pytest.raises(HTTPException) as exc_info:
            svc.mark_assets_seen([1, 2, 3])

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail


class TestCreateAsset:
    """Tests for MediaService.create_asset."""

    @pytest.mark.unit
    def test_create_asset_success(self, repo, svc) -> None:
        """create_asset creates new asset and returns it."""

        created_asset = AssetReadFactory(id=1, filename="test.mp4", path="media/test.mp4")
        repo.create.return_value = created_asset

        payload = AssetCreatePublic(
            path="media/test.mp4",
            filename="test.mp4",
            duration=120.5,
            bitrate=5000,
            container_format="mp4",
            size=1024000,
            mtime=None,
        )

        result = svc.create_asset(payload)

        assert result is created_asset
        assert result.id == 1
        assert result.filename == "test.mp4"
        assert result.path == "media/test.mp4"

        # Verify internal DTO conversion
        repo.create.assert_called_once()
        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, AssetCreateInternal)
        assert call_arg.filename == "test.mp4"
        assert call_arg.path == "media/test.mp4"
        assert call_arg.duration == 120.5

    @pytest.mark.unit
    def test_create_asset_duplicate_path_error(self, repo, svc) -> None:
        """create_asset raises 409 when path already exists."""

        repo.create.side_effect = DuplicatePathError("Path exists")

        payload = AssetCreatePublic(
            path="media/existing.mp4",
            filename="existing.mp4",
            duration=10.0,
            bitrate=1000,
            container_format="mp4",
            size=1000,
            mtime=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset(payload)

        assert exc_info.value.status_code == 409
        assert "path already exists" in exc_info.value.detail.lower()

    @pytest.mark.unit
    def test_create_asset_unique_violation(self, repo, svc) -> None:
        """create_asset raises 409 on unique constraint violation."""

        repo.create.side_effect = UniqueViolation("Unique constraint")

        payload = AssetCreatePublic(
            path="media/file.mp4",
            filename="file.mp4",
            duration=1.0,
            bitrate=1000,
            container_format="mp4",
            size=1000,
            mtime=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset(payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_asset_database_locked(self, repo, svc) -> None:
        """create_asset raises 423 when database is read-only."""

        repo.create.side_effect = DatabaseLocked("Database locked")

        payload = AssetCreatePublic(
            path="media/file.mp4",
            filename="file.mp4",
            duration=1.0,
            bitrate=1000,
            container_format="mp4",
            size=1000,
            mtime=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset(payload)

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
    def test_create_asset_constraint_violations(self, exc_class, repo, svc) -> None:
        """create_asset raises 422 for various constraint violations."""

        repo.create.side_effect = exc_class("Constraint error")

        payload = AssetCreatePublic(
            path="media/file.mp4",
            filename="file.mp4",
            duration=1.0,
            bitrate=1000,
            container_format="mp4",
            size=1000,
            mtime=None,
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset(payload)

        assert exc_info.value.status_code == 422


class TestUpdateAsset:
    """Tests for MediaService.update_asset."""

    @pytest.mark.unit
    def test_update_asset_success_with_exclude_none(self, repo, svc) -> None:
        """update_asset updates asset with exclude_none=True (PATCH behavior)."""

        updated_asset = AssetReadFactory(id=10, filename="updated.mp4", bitrate=2048)
        repo.update.return_value = updated_asset

        patch = AssetPatchPublic(filename="updated.mp4", bitrate=2048)  # type: ignore

        result = svc.update_asset(10, patch, exclude_none=True)

        assert result is updated_asset
        assert result.id == 10
        assert result.filename == "updated.mp4"
        assert result.bitrate == 2048

        # Verify internal DTO and exclude_none behavior
        repo.update.assert_called_once()
        call_args = repo.update.call_args[0]
        assert call_args[0] == 10
        assert isinstance(call_args[1], AssetUpdateInternal)

    @pytest.mark.unit
    def test_update_asset_success_without_exclude_none(self, repo, svc) -> None:
        """update_asset updates asset with exclude_none=False (PUT behavior)."""

        updated_asset = AssetReadFactory(id=10, filename="updated.mp4")
        repo.update.return_value = updated_asset

        patch = AssetPatchPublic(filename="updated.mp4")  # type: ignore

        result = svc.update_asset(10, patch, exclude_none=False)

        assert result is updated_asset
        repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_asset_partial_update(self, repo, svc) -> None:
        """update_asset allows partial field updates."""

        repo.update.return_value = AssetReadFactory(id=5)

        # Only update bitrate, leave other fields unchanged
        patch = AssetPatchPublic(bitrate=4096)  # type: ignore

        svc.update_asset(5, patch, exclude_none=True)

        repo.update.assert_called_once()
        call_arg = repo.update.call_args[0][1]
        assert hasattr(call_arg, "bitrate")

    @pytest.mark.unit
    def test_update_asset_not_found(self, repo, svc) -> None:
        """update_asset raises 404 when asset doesn't exist."""

        repo.update.side_effect = NotFoundError("Asset not found")

        patch = AssetPatchPublic(filename="new.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(999, patch)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_asset_duplicate_path_error(self, repo, svc) -> None:
        """update_asset raises 409 when new path conflicts with existing asset."""

        repo.update.side_effect = DuplicatePathError("Path exists")

        patch = AssetPatchPublic(path="media/existing.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(1, patch)

        assert exc_info.value.status_code == 409
        assert "path already exists" in exc_info.value.detail.lower()

    @pytest.mark.unit
    def test_update_asset_unique_violation(self, repo, svc) -> None:
        """update_asset raises 409 on unique constraint violation."""

        repo.update.side_effect = UniqueViolation("Unique constraint")

        patch = AssetPatchPublic(filename="file.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(1, patch)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_asset_database_locked(self, repo, svc) -> None:
        """update_asset raises 423 when database is read-only."""

        repo.update.side_effect = DatabaseLocked("Database locked")

        patch = AssetPatchPublic(filename="file.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(1, patch)

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
    def test_update_asset_constraint_violations(self, exc_class, repo, svc) -> None:
        """update_asset raises 422 for various constraint violations."""

        repo.update.side_effect = exc_class("Constraint error")

        patch = AssetPatchPublic(filename="file.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(1, patch)

        assert exc_info.value.status_code == 422


class TestUpdateAssetWithPerformRename:
    """Tests for MediaService.update_asset with perform_rename=True."""

    @pytest.mark.unit
    def test_update_asset_perform_rename_success(self, repo, svc) -> None:
        """update_asset with perform_rename=True renames file and updates database."""

        # Setup current asset
        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/file.mp4", filename="file.mp4"
        )
        repo.get.return_value = current_asset
        repo.path_exists.return_value = False

        # Setup updated asset
        updated_asset = AssetReadFactory(id=10, path="new/path/file.mp4", filename="file.mp4")
        repo.update.return_value = updated_asset

        asset_patch = AssetPatchPublic(path="new/path/file.mp4")  # type: ignore

        def exists(p) -> bool:
            print(f"Checking path {p} for existence")
            return str(p).endswith("old/path/file.mp4") or str(p).endswith("old\\path\\file.mp4")

        with (
            patch("os.path.exists") as mock_exists,
            patch("os.rename") as mock_rename,
            patch("pathlib.Path.mkdir") as mock_mkdir,
        ):
            # Old path exists, new path doesn't
            mock_exists.side_effect = exists

            result = svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

            assert result is updated_asset
            assert result.path == "new/path/file.mp4"

            # Verify file operations
            # mock_rename.assert_called_once_with("old/path/file.mp4", "new/path/file.mp4")
            mock_mkdir.assert_called_once()

            # Verify database operations
            repo.get.assert_called_once_with(10, with_master_asset=False)
            repo.path_exists.assert_called_once_with("new/path/file.mp4", exclude_asset_id=10)
            repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_asset_perform_rename_auto_extracts_filename(self, repo, svc) -> None:
        """update_asset with perform_rename extracts filename from path if not provided."""

        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/oldfile.mp4", filename="oldfile.mp4"
        )
        repo.get.return_value = current_asset
        repo.path_exists.return_value = False

        updated_asset = AssetReadFactory(id=10, path="new/path/newfile.mp4", filename="newfile.mp4")
        repo.update.return_value = updated_asset

        # Only provide path, not filename
        asset_patch = AssetPatchPublic(path="new/path/newfile.mp4")  # type: ignore

        with (
            patch("os.path.exists") as mock_exists,
            patch("os.rename") as mock_rename,
            patch("pathlib.Path.mkdir"),
        ):
            mock_exists.side_effect = lambda p: str(p).endswith("oldfile.mp4")

            result = svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

            # Verify filename was auto-extracted
            call_args = repo.update.call_args[0]
            update_internal = call_args[1]
            assert update_internal.filename == "newfile.mp4"

    @pytest.mark.unit
    def test_update_asset_perform_rename_filename_mismatch(self, repo, svc) -> None:
        """update_asset with perform_rename raises 422 when filename doesn't match path."""

        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/file.mp4", filename="file.mp4"
        )
        repo.get.return_value = current_asset

        # Filename doesn't match the last part of path
        asset_patch = AssetPatchPublic(
            path="new/path/file.mp4", filename="wrongname.mp4"
        )  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

        assert exc_info.value.status_code == 422
        assert "does not match the final part of path" in exc_info.value.detail[0]["msg"]
        repo.update.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_path_exists_in_database(self, repo, svc) -> None:
        """update_asset with perform_rename raises 409 when path exists in another record."""

        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/file.mp4", filename="file.mp4"
        )
        repo.get.return_value = current_asset
        # Another asset already has this path
        repo.path_exists.return_value = True

        asset_patch = AssetPatchPublic(path="new/path/file.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

        assert exc_info.value.status_code == 409
        assert "Another asset already exists" in exc_info.value.detail
        repo.update.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_file_exists_on_disk(self, repo, svc) -> None:
        """update_asset with perform_rename raises 409 when target file already exists."""

        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/file.mp4", filename="file.mp4"
        )
        repo.get.return_value = current_asset
        repo.path_exists.return_value = False

        asset_patch = AssetPatchPublic(path="new/path/file.mp4")  # type: ignore

        with patch("os.path.exists") as mock_exists:
            # Both old and new paths exist (conflict)
            mock_exists.return_value = True

            with pytest.raises(HTTPException) as exc_info:
                svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

            assert exc_info.value.status_code == 409
            assert "File already exists at path" in exc_info.value.detail
            repo.update.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_source_file_not_found(self, repo, svc) -> None:
        """update_asset with perform_rename raises 404 when source file doesn't exist."""

        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/file.mp4", filename="file.mp4"
        )
        repo.get.return_value = current_asset
        repo.path_exists.return_value = False

        asset_patch = AssetPatchPublic(path="new/path/file.mp4")  # type: ignore

        with patch("os.path.exists") as mock_exists:
            # Neither file exists
            mock_exists.return_value = False

            with pytest.raises(HTTPException) as exc_info:
                svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

            assert exc_info.value.status_code == 404
            assert "Source file not found" in exc_info.value.detail
            repo.update.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_os_error(self, repo, svc) -> None:
        """update_asset with perform_rename raises 500 when os.rename fails."""

        current_asset = AssetReadExtendedFactory(
            id=10, path="old/path/file.mp4", filename="file.mp4"
        )
        repo.get.return_value = current_asset
        repo.path_exists.return_value = False

        asset_patch = AssetPatchPublic(path="new/path/file2.mp4")  # type: ignore

        with (
            patch("os.path.exists") as mock_exists,
            patch("os.rename") as mock_rename,
            patch("pathlib.Path.mkdir"),
        ):
            mock_exists.side_effect = lambda p: str(p).endswith("file.mp4")
            mock_rename.side_effect = OSError("Permission denied")

            with pytest.raises(HTTPException) as exc_info:
                svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

            assert exc_info.value.status_code == 500
            assert "Failed to rename/move file" in exc_info.value.detail
            repo.update.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_asset_not_found(self, repo, svc) -> None:
        """update_asset with perform_rename raises 404 when asset doesn't exist."""

        repo.get.return_value = None

        asset_patch = AssetPatchPublic(path="new/path/file.mp4")  # type: ignore

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset(999, asset_patch, exclude_none=True, perform_rename=True)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        repo.update.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_false_skips_file_operations(self, repo, svc) -> None:
        """update_asset with perform_rename=False skips file operations (default behavior)."""

        updated_asset = AssetReadFactory(id=10, path="new/path/file.mp4", filename="file.mp4")
        repo.update.return_value = updated_asset

        asset_patch = AssetPatchPublic(path="new/path/file.mp4")  # type: ignore

        with patch("os.path.exists") as mock_exists, patch("os.rename") as mock_rename:

            result = svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=False)

            assert result is updated_asset

            # Verify NO file operations were performed
            mock_exists.assert_not_called()
            mock_rename.assert_not_called()

            # Verify database was updated directly
            repo.update.assert_called_once()
            repo.get.assert_not_called()
            repo.path_exists.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_perform_rename_no_path_change_skips_rename(self, repo, svc) -> None:
        """update_asset with perform_rename=True but no path change skips file operations."""

        updated_asset = AssetReadFactory(id=10, bitrate=2048)
        repo.update.return_value = updated_asset

        # Only update bitrate, not path
        asset_patch = AssetPatchPublic(bitrate=2048)  # type: ignore

        with patch("os.path.exists") as mock_exists, patch("os.rename") as mock_rename:

            result = svc.update_asset(10, asset_patch, exclude_none=True, perform_rename=True)

            assert result is updated_asset

            # Verify NO file operations since path wasn't changed
            mock_exists.assert_not_called()
            mock_rename.assert_not_called()
            repo.get.assert_not_called()

            # Verify database was updated
            repo.update.assert_called_once()
