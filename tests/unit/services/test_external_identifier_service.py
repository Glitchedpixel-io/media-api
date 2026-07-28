"""Unit tests for ExternalIdentifierService."""

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
from app.repositories.protocols import (
    ExternalIdentifierRepository,
    MediaRepository,
    TitleRepository,
)
from app.schemas import (
    ExternalIdentifierCreatePublic,
    ExternalIdentifierPatchPublic,
    ExternalIdentifierRead,
    ExternalIdentifierReadExtended,
    ExternalIdResolution,
    IdSchemeRead,
)
from app.schemas.enums import EntityTypeEnum
from app.services import ExternalIdentifierService


class TestResolve:
    """Tests for ExternalIdentifierService.resolve."""

    @pytest.mark.unit
    def test_resolve_success_asset(self) -> None:
        """resolve returns resolution for existing asset external ID."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_id_repo.resolve_by_code.return_value = (EntityTypeEnum.asset, 42, 1)
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        result = svc.resolve("imdb", "tt1234567")

        assert isinstance(result, ExternalIdResolution)
        assert result.entity_type == EntityTypeEnum.asset
        assert result.entity_id == 42
        assert result.scheme_code == "imdb"
        assert result.external_id == "tt1234567"
        ext_id_repo.resolve_by_code.assert_called_once_with("imdb", "tt1234567")

    @pytest.mark.unit
    def test_resolve_success_title(self) -> None:
        """resolve returns resolution for existing title external ID."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_id_repo.resolve_by_code.return_value = (EntityTypeEnum.title, 99, 2)
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        result = svc.resolve("tmdb", "12345")

        assert result.entity_type == EntityTypeEnum.title
        assert result.entity_id == 99
        assert result.scheme_code == "tmdb"
        assert result.external_id == "12345"

    @pytest.mark.unit
    def test_resolve_not_found(self) -> None:
        """resolve raises 404 when external ID doesn't exist."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_id_repo.resolve_by_code.return_value = None
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.resolve("nonexistent", "abc123")

        assert exc_info.value.status_code == 404
        assert "External ID not found" in exc_info.value.detail
        assert "nonexistent:abc123" in exc_info.value.detail


class TestListForEntity:
    """Tests for ExternalIdentifierService.list_for_entity."""

    @pytest.mark.unit
    def test_list_for_entity_asset_success(self) -> None:
        """list_for_entity returns list of external IDs for asset."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_ids = [
            ExternalIdentifierReadExtended(
                id=1,
                entity_type=EntityTypeEnum.asset,
                entity_id=42,
                scheme_id=1,
                external_id="tt123",
                created_at="2025-01-01T00:00:00Z",
                scheme=IdSchemeRead(id=1, code="imdb", label="IMDb", validator=None),
            )
        ]
        ext_id_repo.list_for_entity.return_value = ext_ids
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        result = svc.list_for_entity(EntityTypeEnum.asset, 42)

        assert len(result) == 1
        assert result[0].external_id == "tt123"
        ext_id_repo.list_for_entity.assert_called_once_with(EntityTypeEnum.asset, 42)

    @pytest.mark.unit
    def test_list_for_entity_empty(self) -> None:
        """list_for_entity returns empty list when no external IDs exist."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_id_repo.list_for_entity.return_value = []
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        result = svc.list_for_entity(EntityTypeEnum.title, 99)

        assert result == []


class TestCreateForEntity:
    """Tests for ExternalIdentifierService.create_for_entity."""

    @pytest.mark.unit
    def test_create_for_entity_asset_success(self) -> None:
        """create_for_entity creates external ID for asset when asset exists."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        media_repo.exists.return_value = True
        created = ExternalIdentifierRead(
            id=1,
            entity_type=EntityTypeEnum.asset,
            entity_id=42,
            scheme_id=1,
            external_id="tt123",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.create.return_value = created
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=1, external_id="tt123")
        result = svc.create_for_entity(EntityTypeEnum.asset, 42, payload)

        assert result == created
        media_repo.exists.assert_called_once_with(42)
        ext_id_repo.create.assert_called_once()

    @pytest.mark.unit
    def test_create_for_entity_title_success(self) -> None:
        """create_for_entity creates external ID for title when title exists."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        title_repo.exists.return_value = True
        created = ExternalIdentifierRead(
            id=2,
            entity_type=EntityTypeEnum.title,
            entity_id=99,
            scheme_id=2,
            external_id="12345",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.create.return_value = created
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=2, external_id="12345")
        result = svc.create_for_entity(EntityTypeEnum.title, 99, payload)

        assert result == created
        title_repo.exists.assert_called_once_with(99)

    @pytest.mark.unit
    def test_create_for_entity_asset_not_found(self) -> None:
        """create_for_entity raises 404 when asset doesn't exist."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        media_repo.exists.return_value = False
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=1, external_id="tt123")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_for_entity(EntityTypeEnum.asset, 42, payload)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        ext_id_repo.create.assert_not_called()

    @pytest.mark.unit
    def test_create_for_entity_title_not_found(self) -> None:
        """create_for_entity raises 404 when title doesn't exist."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        title_repo.exists.return_value = False
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=1, external_id="123")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_for_entity(EntityTypeEnum.title, 99, payload)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_for_entity_unique_violation(self) -> None:
        """create_for_entity raises 409 on unique constraint violation."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        media_repo.exists.return_value = True
        ext_id_repo.create.side_effect = UniqueViolation("duplicate")
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=1, external_id="tt123")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_for_entity(EntityTypeEnum.asset, 42, payload)

        assert exc_info.value.status_code == 409

    @pytest.mark.unit
    def test_create_for_entity_database_locked(self) -> None:
        """create_for_entity raises 423 when database is read-only."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        media_repo.exists.return_value = True
        ext_id_repo.create.side_effect = DatabaseLocked("locked")
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=1, external_id="tt123")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_for_entity(EntityTypeEnum.asset, 42, payload)

        assert exc_info.value.status_code == 423

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
    def test_create_for_entity_constraint_violations(self, exc_class) -> None:
        """create_for_entity raises 422 for various constraint violations."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        media_repo.exists.return_value = True
        ext_id_repo.create.side_effect = exc_class("constraint")
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        payload = ExternalIdentifierCreatePublic(scheme_id=1, external_id="tt123")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_for_entity(EntityTypeEnum.asset, 42, payload)

        assert exc_info.value.status_code == 422


class TestUpdateForEntity:
    """Tests for ExternalIdentifierService.update_for_entity."""

    @pytest.mark.unit
    def test_update_for_entity_success(self) -> None:
        """update_for_entity updates external ID successfully."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        existing = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.asset,
            entity_id=42,
            scheme_id=1,
            external_id="old",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.get.return_value = existing
        updated = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.asset,
            entity_id=42,
            scheme_id=1,
            external_id="new",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.update.return_value = updated
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        patch = ExternalIdentifierPatchPublic(external_id="new")
        result = svc.update_for_entity(EntityTypeEnum.asset, 42, 10, patch)

        assert result.external_id == "new"
        ext_id_repo.get.assert_called_once_with(10)
        ext_id_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_for_entity_not_found_missing(self) -> None:
        """update_for_entity raises 404 when record doesn't exist."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_id_repo.get.return_value = None
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        patch = ExternalIdentifierPatchPublic(external_id="new")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_for_entity(EntityTypeEnum.asset, 42, 10, patch)

        assert exc_info.value.status_code == 404

    @pytest.mark.unit
    def test_update_for_entity_not_found_wrong_entity_type(self) -> None:
        """update_for_entity raises 404 when entity type doesn't match."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        # External ID belongs to a title, not an asset
        existing = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.title,
            entity_id=99,
            scheme_id=1,
            external_id="old",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.get.return_value = existing
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        patch = ExternalIdentifierPatchPublic(external_id="new")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_for_entity(EntityTypeEnum.asset, 42, 10, patch)

        assert exc_info.value.status_code == 404

    @pytest.mark.unit
    def test_update_for_entity_not_found_wrong_entity_id(self) -> None:
        """update_for_entity raises 404 when entity ID doesn't match."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        # External ID belongs to asset 99, not 42
        existing = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.asset,
            entity_id=99,
            scheme_id=1,
            external_id="old",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.get.return_value = existing
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        patch = ExternalIdentifierPatchPublic(external_id="new")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_for_entity(EntityTypeEnum.asset, 42, 10, patch)

        assert exc_info.value.status_code == 404

    @pytest.mark.unit
    def test_update_for_entity_unique_violation(self) -> None:
        """update_for_entity raises 409 on unique constraint violation."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        existing = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.asset,
            entity_id=42,
            scheme_id=1,
            external_id="old",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.get.return_value = existing
        ext_id_repo.update.side_effect = UniqueViolation("duplicate")
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        patch = ExternalIdentifierPatchPublic(external_id="new")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_for_entity(EntityTypeEnum.asset, 42, 10, patch)

        assert exc_info.value.status_code == 409


class TestDeleteForEntity:
    """Tests for ExternalIdentifierService.delete_for_entity."""

    @pytest.mark.unit
    def test_delete_for_entity_success(self) -> None:
        """delete_for_entity deletes external ID successfully."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        existing = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.asset,
            entity_id=42,
            scheme_id=1,
            external_id="test",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.get.return_value = existing
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        svc.delete_for_entity(EntityTypeEnum.asset, 42, 10)

        ext_id_repo.get.assert_called_once_with(10)
        ext_id_repo.delete.assert_called_once_with(10)

    @pytest.mark.unit
    def test_delete_for_entity_not_found_missing(self) -> None:
        """delete_for_entity raises 404 when record doesn't exist."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        ext_id_repo.get.return_value = None
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_for_entity(EntityTypeEnum.asset, 42, 10)

        assert exc_info.value.status_code == 404
        ext_id_repo.delete.assert_not_called()

    @pytest.mark.unit
    def test_delete_for_entity_not_found_wrong_entity(self) -> None:
        """delete_for_entity raises 404 when entity doesn't match."""
        ext_id_repo = create_autospec(ExternalIdentifierRepository, instance=True, spec_set=True)
        media_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        title_repo = create_autospec(TitleRepository, instance=True, spec_set=True)

        # External ID belongs to asset 99, not 42
        existing = ExternalIdentifierRead(
            id=10,
            entity_type=EntityTypeEnum.asset,
            entity_id=99,
            scheme_id=1,
            external_id="test",
            created_at="2025-01-01T00:00:00Z",
        )
        ext_id_repo.get.return_value = existing
        svc = ExternalIdentifierService(ext_id_repo, media_repo, title_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_for_entity(EntityTypeEnum.asset, 42, 10)

        assert exc_info.value.status_code == 404
        ext_id_repo.delete.assert_not_called()
