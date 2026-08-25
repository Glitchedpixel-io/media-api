"""Unit tests for TitleContentService."""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.models.title_contents import ContentKind
from app.repositories import MediaRepository
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
from app.repositories.protocols import TitleContentRepository, TitleRepository
from app.schemas import (
    TitleContentInsert,
    TitleContentPatchPublic,
    TitleContentUpdateInternal,
)
from app.services import TitleContentService
from tests.factories import TitleContentReadFactory


class TestInsertPositioned:
    """Tests for TitleContentService.insert_positioned."""

    @pytest.mark.unit
    def test_insert_positioned_success_at_end(self) -> None:
        """insert_positioned creates content at end position successfully."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        created_content = TitleContentReadFactory(
            id=100, parent_title_id=5, child_title_id=10, kind=ContentKind.title
        )
        c_repo.create_positioned.return_value = created_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)
        result = svc.insert_positioned(5, insert, position="end")

        assert result is created_content
        assert result.id == 100
        assert result.parent_title_id == 5
        assert result.child_title_id == 10
        t_repo.exists.assert_called_once_with(5)
        c_repo.create_positioned.assert_called_once_with(
            5, insert, before_id=None, after_id=None, position="end"
        )

    @pytest.mark.unit
    def test_insert_positioned_success_with_before_id(self) -> None:
        """insert_positioned inserts content before specified ID."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        created_content = TitleContentReadFactory(parent_title_id=5)
        c_repo.create_positioned.return_value = created_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)
        result = svc.insert_positioned(5, insert, before_id=3)

        assert result is created_content
        c_repo.create_positioned.assert_called_once_with(
            5, insert, before_id=3, after_id=None, position=None
        )

    @pytest.mark.unit
    def test_insert_positioned_success_with_after_id(self) -> None:
        """insert_positioned inserts content after specified ID."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        created_content = TitleContentReadFactory(parent_title_id=5)
        c_repo.create_positioned.return_value = created_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)
        result = svc.insert_positioned(5, insert, after_id=7)

        assert result is created_content
        c_repo.create_positioned.assert_called_once_with(
            5, insert, before_id=None, after_id=7, position=None
        )

    @pytest.mark.unit
    def test_insert_positioned_with_asset(self) -> None:
        """insert_positioned works with asset kind content."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        created_content = TitleContentReadFactory(
            parent_title_id=5, asset_id=42, kind=ContentKind.asset
        )
        c_repo.create_positioned.return_value = created_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(asset_id=42, kind=ContentKind.asset)
        result = svc.insert_positioned(5, insert)

        assert result is created_content
        assert result.asset_id == 42
        assert result.kind == ContentKind.asset

    @pytest.mark.unit
    def test_insert_positioned_parent_title_not_found(self) -> None:
        """insert_positioned raises 404 when parent title doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = False
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)

        with pytest.raises(HTTPException) as exc_info:
            svc.insert_positioned(999, insert)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        t_repo.exists.assert_called_once_with(999)
        c_repo.create_positioned.assert_not_called()

    @pytest.mark.unit
    def test_insert_positioned_unique_violation(self) -> None:
        """insert_positioned raises 409 on unique constraint violation."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.create_positioned.side_effect = UniqueViolation("Duplicate entry")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)

        with pytest.raises(HTTPException) as exc_info:
            svc.insert_positioned(5, insert)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_insert_positioned_database_locked(self) -> None:
        """insert_positioned raises 423 when database is read-only."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.create_positioned.side_effect = DatabaseLocked("Database locked")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)

        with pytest.raises(HTTPException) as exc_info:
            svc.insert_positioned(5, insert)

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
    def test_insert_positioned_constraint_violations(self, exc_class) -> None:
        """insert_positioned raises 422 for various constraint violations."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.create_positioned.side_effect = exc_class("Constraint error")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        insert = TitleContentInsert(child_title_id=10, kind=ContentKind.title)

        with pytest.raises(HTTPException) as exc_info:
            svc.insert_positioned(5, insert)

        assert exc_info.value.status_code == 422


class TestGetTitleContent:
    """Tests for TitleContentService.get_title_content."""

    @pytest.mark.unit
    def test_get_title_content_success(self) -> None:
        """get_title_content returns list of content items for title."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        content_items = [
            TitleContentReadFactory(parent_title_id=9, id=1),
            TitleContentReadFactory(parent_title_id=9, id=2),
            TitleContentReadFactory(parent_title_id=9, id=3),
        ]
        c_repo.list_title_content.return_value = content_items
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.get_title_content(9)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].id == 1
        assert result[1].id == 2
        assert result[2].id == 3
        t_repo.exists.assert_called_once_with(9)
        c_repo.list_title_content.assert_called_once_with(9, True)

    @pytest.mark.unit
    def test_get_title_content_empty_list(self) -> None:
        """get_title_content returns empty list when title has no content."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.list_title_content.return_value = []
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.get_title_content(9)

        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.unit
    def test_get_title_content_title_not_found(self) -> None:
        """get_title_content raises 404 when title doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = False
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_title_content(999)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        t_repo.exists.assert_called_once_with(999)
        c_repo.list_title_content.assert_not_called()

    @pytest.mark.unit
    def test_get_title_content_calls_with_extended_flag(self) -> None:
        """get_title_content passes True flag for extended data."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.list_title_content.return_value = []
        svc = TitleContentService(t_repo, c_repo, m_repo)

        svc.get_title_content(5)

        # Verify the extended flag is True
        call_args = c_repo.list_title_content.call_args[0]
        assert call_args[0] == 5
        assert call_args[1] is True


class TestUpdateTitleContent:
    """Tests for TitleContentService.update_title_content."""

    @pytest.mark.unit
    def test_update_title_content_success_with_exclude_none(self) -> None:
        """update_title_content updates content with exclude_none=True."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        updated_content = TitleContentReadFactory(id=7, parent_title_id=3, label="Updated")
        c_repo.update.return_value = updated_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="Updated")

        result = svc.update_title_content(3, 7, patch, exclude_none=True)

        assert result is updated_content
        assert result.id == 7
        assert result.label == "Updated"

        # Verify internal DTO includes parent_title_id
        c_repo.update.assert_called_once()
        call_args = c_repo.update.call_args[0]
        assert call_args[0] == 7
        assert isinstance(call_args[1], TitleContentUpdateInternal)
        assert call_args[1].parent_title_id == 3

    @pytest.mark.unit
    def test_update_title_content_success_without_exclude_none(self) -> None:
        """update_title_content updates content with exclude_none=False."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        updated_content = TitleContentReadFactory(id=7)
        c_repo.update.return_value = updated_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="Label")

        result = svc.update_title_content(3, 7, patch, exclude_none=False)

        assert result is updated_content
        c_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_title_content_partial_update(self) -> None:
        """update_title_content allows updating only label field."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        c_repo.update.return_value = TitleContentReadFactory()
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="New Label")

        svc.update_title_content(3, 7, patch, exclude_none=True)

        call_arg = c_repo.update.call_args[0][1]
        assert hasattr(call_arg, "label")
        assert call_arg.parent_title_id == 3

    @pytest.mark.unit
    def test_update_title_content_not_found(self) -> None:
        """update_title_content raises 404 when content doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        c_repo.update.side_effect = NotFoundError("Content not found")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="Label")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_content(3, 999, patch, exclude_none=True)

        assert exc_info.value.status_code == 404
        assert "Title Reference not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_title_content_unique_violation(self) -> None:
        """update_title_content raises 409 on unique constraint violation."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        c_repo.update.side_effect = UniqueViolation("Unique constraint")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="Label")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_content(3, 7, patch, exclude_none=False)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_title_content_database_locked(self) -> None:
        """update_title_content raises 423 when database is read-only."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        c_repo.update.side_effect = DatabaseLocked("Database locked")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="Label")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_content(3, 7, patch, exclude_none=True)

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
    def test_update_title_content_constraint_violations(self, exc_class) -> None:
        """update_title_content raises 422 for various constraint violations."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        c_repo.update.side_effect = exc_class("Constraint error")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        patch = TitleContentPatchPublic(label="Label")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_content(3, 7, patch, exclude_none=True)

        assert exc_info.value.status_code == 422


class TestReorderContent:
    """Tests for TitleContentService.reorder_content."""

    @pytest.mark.unit
    def test_reorder_content_success_with_after_id(self) -> None:
        """reorder_content moves content after specified ID successfully."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        reordered_content = TitleContentReadFactory(id=77, parent_title_id=4)
        c_repo.reorder.return_value = reordered_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.reorder_content(4, title_content_id=77, after_id=2)

        assert result is reordered_content
        assert result.id == 77
        t_repo.exists.assert_called_once_with(4)
        c_repo.reorder.assert_called_once_with(4, 77, before_id=None, after_id=2, position=None)

    @pytest.mark.unit
    def test_reorder_content_success_with_before_id(self) -> None:
        """reorder_content moves content before specified ID successfully."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        reordered_content = TitleContentReadFactory(id=77)
        c_repo.reorder.return_value = reordered_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.reorder_content(4, title_content_id=77, before_id=5)

        assert result is reordered_content
        c_repo.reorder.assert_called_once_with(4, 77, before_id=5, after_id=None, position=None)

    @pytest.mark.unit
    def test_reorder_content_success_with_position(self) -> None:
        """reorder_content moves content to position string (start/end)."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        reordered_content = TitleContentReadFactory(id=77)
        c_repo.reorder.return_value = reordered_content
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.reorder_content(4, title_content_id=77, position="start")

        assert result is reordered_content
        c_repo.reorder.assert_called_once_with(
            4, 77, before_id=None, after_id=None, position="start"
        )

    @pytest.mark.unit
    def test_reorder_content_parent_title_not_found(self) -> None:
        """reorder_content raises 404 when parent title doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = False
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(999, title_content_id=77)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        t_repo.exists.assert_called_once_with(999)
        c_repo.reorder.assert_not_called()

    @pytest.mark.unit
    def test_reorder_content_title_content_not_found_via_exception(self) -> None:
        """reorder_content raises 404 when repository raises NotFoundError."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.reorder.side_effect = NotFoundError("Content not found")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(4, title_content_id=999)

        assert exc_info.value.status_code == 404
        assert "Title Content not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_reorder_content_returns_none_raises_404(self) -> None:
        """reorder_content raises 404 when repository returns None."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.reorder.return_value = None
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(4, title_content_id=99, position="start")

        assert exc_info.value.status_code == 404
        assert "Title Content not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_reorder_content_unique_violation(self) -> None:
        """reorder_content raises 409 on unique constraint violation."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.reorder.side_effect = UniqueViolation("Unique constraint")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(4, title_content_id=77, after_id=2)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_reorder_content_database_locked(self) -> None:
        """reorder_content raises 423 when database is read-only."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.reorder.side_effect = DatabaseLocked("Database locked")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(4, title_content_id=77)

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
    def test_reorder_content_constraint_violations(self, exc_class) -> None:
        """reorder_content raises 422 for various constraint violations."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.reorder.side_effect = exc_class("Constraint error")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(4, title_content_id=77)

        assert exc_info.value.status_code == 422

    @pytest.mark.unit
    def test_reorder_content_unexpected_exception_raises_500(self) -> None:
        """reorder_content raises 500 on unexpected exceptions."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.reorder.side_effect = RuntimeError("Unexpected error")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.reorder_content(4, title_content_id=77)

        assert exc_info.value.status_code == 500
        assert "Internal server error" in exc_info.value.detail


class TestGetTitlesWithAsset:
    """Tests for TitleContentService.get_titles_with_asset."""

    @pytest.mark.unit
    def test_get_titles_with_asset_success(self) -> None:
        """get_titles_with_asset returns list of titles for existing asset."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        from app.schemas import TitleContentReadParent, TitleRead

        title_content_items = [
            TitleContentReadParent(
                id=1,
                parent_title_id=10,
                order_key="A",
                kind=ContentKind.asset,
                child_title_id=None,
                asset_id=42,
                label="First",
                parent_title=TitleRead(id=10, name="Title 1", title_type="movie"),
            ),
            TitleContentReadParent(
                id=2,
                parent_title_id=20,
                order_key="B",
                kind=ContentKind.asset,
                child_title_id=None,
                asset_id=42,
                label="Second",
                parent_title=TitleRead(id=20, name="Title 2", title_type="season"),
            ),
        ]
        c_repo.get_titles_with_asset.return_value = title_content_items
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.get_titles_with_asset(42)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0].parent_title_id == 10
        assert result[1].parent_title_id == 20
        m_repo.exists.assert_called_once_with(42)
        c_repo.get_titles_with_asset.assert_called_once_with(42)

    @pytest.mark.unit
    def test_get_titles_with_asset_empty_list(self) -> None:
        """get_titles_with_asset returns empty list when asset has no titles."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = True
        c_repo.get_titles_with_asset.return_value = []
        svc = TitleContentService(t_repo, c_repo, m_repo)

        result = svc.get_titles_with_asset(42)

        assert isinstance(result, list)
        assert len(result) == 0
        m_repo.exists.assert_called_once_with(42)

    @pytest.mark.unit
    def test_get_titles_with_asset_not_found(self) -> None:
        """get_titles_with_asset raises 404 when asset doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        m_repo.exists.return_value = False
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_titles_with_asset(999)

        assert exc_info.value.status_code == 404
        assert "Asset not found" in exc_info.value.detail
        m_repo.exists.assert_called_once_with(999)
        c_repo.get_titles_with_asset.assert_not_called()


class TestUnlinkContent:
    """Tests for TitleContentService.unlink_content."""

    @pytest.mark.unit
    def test_unlink_content_success(self) -> None:
        """unlink_content deletes title content successfully."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        svc = TitleContentService(t_repo, c_repo, m_repo)

        # Should not raise an exception
        svc.unlink_content(8, 123)

        t_repo.exists.assert_called_once_with(8)
        c_repo.delete_title_content.assert_called_once_with(123)

    @pytest.mark.unit
    def test_unlink_content_with_different_ids(self) -> None:
        """unlink_content correctly passes IDs to repository."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        svc = TitleContentService(t_repo, c_repo, m_repo)

        svc.unlink_content(parent_title_id=50, title_content_id=75)

        t_repo.exists.assert_called_once_with(50)
        c_repo.delete_title_content.assert_called_once_with(75)

    @pytest.mark.unit
    def test_unlink_content_parent_title_not_found(self) -> None:
        """unlink_content raises 404 when parent title doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = False
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.unlink_content(999, 123)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        t_repo.exists.assert_called_once_with(999)
        c_repo.delete_title_content.assert_not_called()

    @pytest.mark.unit
    def test_unlink_content_database_locked(self) -> None:
        """unlink_content raises 423 when database is read-only."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.delete_title_content.side_effect = DatabaseLocked("Database locked")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.unlink_content(8, 123)

        assert exc_info.value.status_code == 423
        assert "read-only mode" in exc_info.value.detail

    @pytest.mark.unit
    def test_unlink_content_unexpected_exception_raises_500(self) -> None:
        """unlink_content raises 500 on unexpected exceptions."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        c_repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
        m_repo = create_autospec(MediaRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        c_repo.delete_title_content.side_effect = RuntimeError("Unexpected error")
        svc = TitleContentService(t_repo, c_repo, m_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.unlink_content(8, 123)

        assert exc_info.value.status_code == 500
        assert "Internal server error" in exc_info.value.detail
