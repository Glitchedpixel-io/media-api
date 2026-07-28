"""Unit tests for MetadataService."""

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
from app.repositories.protocols import MediaRepository, MetadataRepository
from app.schemas import (
    MetadataCreateInternal,
    MetadataCreatePublic,
    MetadataPatchPublic,
    MetadataUpdateInternal,
)
from app.services import MetadataService
from tests.factories import MetadataReadFactory


class TestGetAssetMetadata:
    """Tests for MetadataService.get_asset_metadata."""

    @pytest.mark.unit
    def test_get_asset_metadata_success(self) -> None:
        """get_asset_metadata returns list of metadata for asset."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        metadata_items = [MetadataReadFactory() for _ in range(3)]
        repo.get_asset_metadata.return_value = metadata_items
        svc = MetadataService(repo, m_repo)

        result = svc.get_asset_metadata(11)

        assert isinstance(result, list)
        assert len(result) == 3
        m_repo.exists.assert_called_once_with(11)
        repo.get_asset_metadata.assert_called_once_with(11)

    @pytest.mark.unit
    def test_get_asset_metadata_empty_list(self) -> None:
        """get_asset_metadata returns empty list when asset has no metadata."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get_asset_metadata.return_value = []
        svc = MetadataService(repo, m_repo)

        result = svc.get_asset_metadata(11)

        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.unit
    def test_get_asset_metadata_asset_not_found(self) -> None:
        """get_asset_metadata raises 404 when asset doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_metadata(999)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.exists.assert_called_once_with(999)
        repo.get_asset_metadata.assert_not_called()


class TestGetAssetMetadataItem:
    """Tests for MetadataService.get_asset_metadata_item."""

    @pytest.mark.unit
    def test_get_asset_metadata_item_success(self) -> None:
        """get_asset_metadata_item returns metadata item when found."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        metadata_item = MetadataReadFactory(id=3, asset_id=22, metadata_type="fp_hash")
        repo.get.return_value = metadata_item
        svc = MetadataService(repo, m_repo)

        result = svc.get_asset_metadata_item(22, 3)

        assert result is metadata_item
        assert result.id == 3
        assert result.asset_id == 22
        m_repo.exists.assert_called_once_with(22)
        repo.get.assert_called_once_with(3)

    @pytest.mark.unit
    def test_get_asset_metadata_item_asset_not_found(self) -> None:
        """get_asset_metadata_item raises 404 when asset doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_metadata_item(22, 3)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.exists.assert_called_once_with(22)
        repo.get.assert_not_called()

    @pytest.mark.unit
    def test_get_asset_metadata_item_metadata_not_found(self) -> None:
        """get_asset_metadata_item raises 404 when metadata item doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get.return_value = None
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_metadata_item(22, 3)

        assert exc_info.value.status_code == 404
        assert "Metadata not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_get_asset_metadata_item_asset_id_mismatch(self) -> None:
        """get_asset_metadata_item raises 404 when metadata belongs to different asset."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        # Metadata belongs to asset 21, not 22
        metadata_item = MetadataReadFactory(id=3, asset_id=21)
        repo.get.return_value = metadata_item
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_asset_metadata_item(22, 3)

        assert exc_info.value.status_code == 404
        assert "Metadata not found" in exc_info.value.detail


class TestCreateAssetMetadata:
    """Tests for MetadataService.create_asset_metadata."""

    @pytest.mark.unit
    def test_create_asset_metadata_success(self) -> None:
        """create_asset_metadata creates new metadata and returns it."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        created_metadata = MetadataReadFactory(
            id=1, asset_id=5, metadata_type="fp_hash", data={"hash": "abc123"}
        )
        repo.create.return_value = created_metadata
        svc = MetadataService(repo, m_repo)

        payload = MetadataCreatePublic(metadata_type="fp_hash", data={"hash": "abc123"})

        result = svc.create_asset_metadata(5, payload)

        assert result is created_metadata
        assert result.asset_id == 5
        assert result.metadata_type == "fp_hash"

        # Verify internal DTO conversion
        repo.create.assert_called_once()
        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, MetadataCreateInternal)
        assert call_arg.asset_id == 5
        assert call_arg.metadata_type == "fp_hash"
        assert call_arg.data == {"hash": "abc123"}

    @pytest.mark.unit
    def test_create_asset_metadata_asset_not_found(self) -> None:
        """create_asset_metadata raises 404 when asset doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = MetadataService(repo, m_repo)

        payload = MetadataCreatePublic(metadata_type="test", data={})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_metadata(777, payload)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.exists.assert_called_once_with(777)
        repo.create.assert_not_called()

    @pytest.mark.unit
    def test_create_asset_metadata_unique_violation(self) -> None:
        """create_asset_metadata raises 409 on unique constraint violation."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.create.side_effect = UniqueViolation("u")
        svc = MetadataService(repo, m_repo)

        payload = MetadataCreatePublic(metadata_type="test", data={})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_metadata(7, payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_asset_metadata_database_locked(self) -> None:
        """create_asset_metadata raises 423 when database is read-only."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.create.side_effect = DatabaseLocked("locked")
        svc = MetadataService(repo, m_repo)

        payload = MetadataCreatePublic(metadata_type="test", data={})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_metadata(7, payload)

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
    def test_create_asset_metadata_constraint_violations(self, exc_class) -> None:
        """create_asset_metadata raises 422 for various constraint violations."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.create.side_effect = exc_class("c")
        svc = MetadataService(repo, m_repo)

        payload = MetadataCreatePublic(metadata_type="test", data={})

        with pytest.raises(HTTPException) as exc_info:
            svc.create_asset_metadata(7, payload)

        assert exc_info.value.status_code == 422


class TestUpdateAssetMetadata:
    """Tests for MetadataService.update_asset_metadata."""

    @pytest.mark.unit
    def test_update_asset_metadata_success(self) -> None:
        """update_asset_metadata updates metadata successfully."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        existing_metadata = MetadataReadFactory(id=3, asset_id=5, metadata_type="old")
        repo.get.return_value = existing_metadata
        updated_metadata = MetadataReadFactory(id=3, asset_id=5, metadata_type="fingerprint")
        repo.update.return_value = updated_metadata
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="fingerprint")

        result = svc.update_asset_metadata(5, 3, patch)

        assert result is updated_metadata
        assert result.metadata_type == "fingerprint"

        # Verify internal DTO
        repo.update.assert_called_once()
        call_args = repo.update.call_args[0]
        assert call_args[0] == 3
        assert isinstance(call_args[1], MetadataUpdateInternal)

    @pytest.mark.unit
    def test_update_asset_metadata_asset_not_found(self) -> None:
        """update_asset_metadata raises 404 when asset doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        repo.get.assert_not_called()

    @pytest.mark.unit
    def test_update_asset_metadata_metadata_not_found(self) -> None:
        """update_asset_metadata raises 404 when metadata item doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get.return_value = None
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

        assert exc_info.value.status_code == 404
        assert "Metadata not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_asset_metadata_asset_id_mismatch(self) -> None:
        """update_asset_metadata raises 404 when metadata belongs to different asset."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        # Metadata belongs to asset 4, not 5
        existing_metadata = MetadataReadFactory(id=3, asset_id=4)
        repo.get.return_value = existing_metadata
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

        assert exc_info.value.status_code == 404

    @pytest.mark.unit
    def test_update_asset_metadata_not_found_error(self) -> None:
        """update_asset_metadata raises 404 on NotFoundError from repository."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        existing_metadata = MetadataReadFactory(id=3, asset_id=5)
        repo.get.return_value = existing_metadata
        repo.update.side_effect = NotFoundError("nf")
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

        assert exc_info.value.status_code == 404
        assert "Metadata not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_asset_metadata_unique_violation(self) -> None:
        """update_asset_metadata raises 409 on unique constraint violation."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get.return_value = MetadataReadFactory(id=3, asset_id=5)
        repo.update.side_effect = UniqueViolation("u")
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_asset_metadata_database_locked(self) -> None:
        """update_asset_metadata raises 423 when database is read-only."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get.return_value = MetadataReadFactory(id=3, asset_id=5)
        repo.update.side_effect = DatabaseLocked("locked")
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

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
    def test_update_asset_metadata_constraint_violations(self, exc_class) -> None:
        """update_asset_metadata raises 422 for various constraint violations."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get.return_value = MetadataReadFactory(id=3, asset_id=5)
        repo.update.side_effect = exc_class("c")
        svc = MetadataService(repo, m_repo)

        patch = MetadataPatchPublic(metadata_type="x")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_asset_metadata(5, 3, patch)

        assert exc_info.value.status_code == 422


class TestDeleteAssetMetadata:
    """Tests for MetadataService.delete_asset_metadata."""

    @pytest.mark.unit
    def test_delete_asset_metadata_success(self) -> None:
        """delete_asset_metadata deletes metadata successfully."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        metadata_item = MetadataReadFactory(id=3, asset_id=5)
        repo.get.return_value = metadata_item
        svc = MetadataService(repo, m_repo)

        # Should not raise an exception
        svc.delete_asset_metadata(5, 3)

        repo.get.assert_called_once_with(3)
        repo.delete.assert_called_once_with(3)

    @pytest.mark.unit
    def test_delete_asset_metadata_asset_not_found(self) -> None:
        """delete_asset_metadata raises 404 when asset doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_asset_metadata(5, 3)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_delete_asset_metadata_metadata_not_found(self) -> None:
        """delete_asset_metadata raises 404 when metadata item doesn't exist."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        repo.get.return_value = None
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_asset_metadata(5, 33)

        assert exc_info.value.status_code == 404
        assert "Metadata not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_delete_asset_metadata_asset_id_mismatch(self) -> None:
        """delete_asset_metadata raises 404 when metadata belongs to different asset."""
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        repo = create_autospec(MetadataRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        # Metadata belongs to asset 4, not 5
        metadata_item = MetadataReadFactory(id=3, asset_id=4)
        repo.get.return_value = metadata_item
        svc = MetadataService(repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_asset_metadata(5, 3)

        assert exc_info.value.status_code == 404
        assert "Metadata not found" in exc_info.value.detail
        repo.delete.assert_not_called()
