"""Unit tests for TitleReferenceService."""

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
from app.repositories.protocols import TitleReferenceRepository, TitleRepository
from app.schemas import (
    TitleReferenceCreateInternal,
    TitleReferenceCreatePublic,
    TitleReferencePatchPublic,
    TitleReferenceUpdateInternal,
)
from app.services import TitleReferenceService
from tests.factories import TitleReferenceReadFactory


class TestCreateReference:
    """Tests for TitleReferenceService.create_reference."""

    @pytest.mark.unit
    def test_create_reference_success(self) -> None:
        """create_reference creates new reference and returns it."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        created_ref = TitleReferenceReadFactory(
            id=1, title_id=5, reference_type="review", reference_url="https://example.com"
        )
        r_repo.create.return_value = created_ref
        svc = TitleReferenceService(t_repo, r_repo)

        payload = TitleReferenceCreatePublic(
            reference_type="review", reference_url="https://example.com"
        )

        result = svc.create_reference(5, payload)

        assert result is created_ref
        assert result.id == 1
        assert result.title_id == 5
        assert result.reference_type == "review"
        assert result.reference_url == "https://example.com"

        # Verify internal DTO conversion
        r_repo.create.assert_called_once()
        call_arg = r_repo.create.call_args[0][0]
        assert isinstance(call_arg, TitleReferenceCreateInternal)
        assert call_arg.title_id == 5
        assert call_arg.reference_type == "review"
        assert call_arg.reference_url == "https://example.com"

    @pytest.mark.unit
    def test_create_reference_with_different_types(self) -> None:
        """create_reference works with various reference types."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        svc = TitleReferenceService(t_repo, r_repo)

        reference_types = ["review", "article", "summary", "other"]
        for ref_type in reference_types:
            r_repo.reset_mock()
            created_ref = TitleReferenceReadFactory(reference_type=ref_type)
            r_repo.create.return_value = created_ref

            payload = TitleReferenceCreatePublic(
                reference_type=ref_type, reference_url="https://test.com"
            )
            result = svc.create_reference(5, payload)

            assert result.reference_type == ref_type
            call_arg = r_repo.create.call_args[0][0]
            assert call_arg.reference_type == ref_type

    @pytest.mark.unit
    def test_create_reference_unique_violation(self) -> None:
        """create_reference raises 409 on unique constraint violation."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.create.side_effect = UniqueViolation("u")
        svc = TitleReferenceService(t_repo, r_repo)

        payload = TitleReferenceCreatePublic(
            reference_type="review", reference_url="https://example.com"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_reference(5, payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_reference_database_locked(self) -> None:
        """create_reference raises 423 when database is read-only."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.create.side_effect = DatabaseLocked("locked")
        svc = TitleReferenceService(t_repo, r_repo)

        payload = TitleReferenceCreatePublic(
            reference_type="review", reference_url="https://example.com"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_reference(5, payload)

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
    def test_create_reference_constraint_violations(self, exc_class) -> None:
        """create_reference raises 422 for various constraint violations."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.create.side_effect = exc_class("c")
        svc = TitleReferenceService(t_repo, r_repo)

        payload = TitleReferenceCreatePublic(
            reference_type="review", reference_url="https://example.com"
        )

        with pytest.raises(HTTPException) as exc_info:
            svc.create_reference(5, payload)

        assert exc_info.value.status_code == 422


class TestGetTitleReferences:
    """Tests for TitleReferenceService.get_title_references."""

    @pytest.mark.unit
    def test_get_title_references_success(self) -> None:
        """get_title_references returns list of references for title."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        references = [TitleReferenceReadFactory() for _ in range(3)]
        r_repo.list_title_references.return_value = references
        svc = TitleReferenceService(t_repo, r_repo)

        result = svc.get_title_references(77)

        assert isinstance(result, list)
        assert len(result) == 3
        t_repo.exists.assert_called_once_with(77)
        r_repo.list_title_references.assert_called_once_with(77)

    @pytest.mark.unit
    def test_get_title_references_empty_list(self) -> None:
        """get_title_references returns empty list when title has no references."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        r_repo.list_title_references.return_value = []
        svc = TitleReferenceService(t_repo, r_repo)

        result = svc.get_title_references(77)

        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.unit
    def test_get_title_references_title_not_found(self) -> None:
        """get_title_references raises 404 when title doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = False
        svc = TitleReferenceService(t_repo, r_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_title_references(999)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        t_repo.exists.assert_called_once_with(999)
        r_repo.list_title_references.assert_not_called()

    @pytest.mark.unit
    def test_get_title_references_with_different_title_ids(self) -> None:
        """get_title_references correctly handles different title IDs."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        t_repo.exists.return_value = True
        r_repo.list_title_references.return_value = []
        svc = TitleReferenceService(t_repo, r_repo)

        test_ids = [1, 50, 999]
        for title_id in test_ids:
            t_repo.reset_mock()
            r_repo.reset_mock()

            svc.get_title_references(title_id)

            t_repo.exists.assert_called_once_with(title_id)
            r_repo.list_title_references.assert_called_once_with(title_id)


class TestUpdateTitleReference:
    """Tests for TitleReferenceService.update_title_reference."""

    @pytest.mark.unit
    def test_update_title_reference_success_with_exclude_none(self) -> None:
        """update_title_reference updates reference with exclude_none=True."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        updated_ref = TitleReferenceReadFactory(id=9, title_id=3, reference_type="article")
        r_repo.update.return_value = updated_ref
        svc = TitleReferenceService(t_repo, r_repo)

        patch = TitleReferencePatchPublic(reference_type="article")

        result = svc.update_title_reference(3, 9, patch, exclude_none=True)

        assert result is updated_ref
        assert result.id == 9
        assert result.title_id == 3
        assert result.reference_type == "article"

        # Verify internal DTO and title_id inclusion
        r_repo.update.assert_called_once()
        call_args = r_repo.update.call_args[0]
        assert call_args[0] == 9
        assert isinstance(call_args[1], TitleReferenceUpdateInternal)
        assert call_args[1].title_id == 3

    @pytest.mark.unit
    def test_update_title_reference_success_without_exclude_none(self) -> None:
        """update_title_reference updates reference with exclude_none=False."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        updated_ref = TitleReferenceReadFactory(id=9)
        r_repo.update.return_value = updated_ref
        svc = TitleReferenceService(t_repo, r_repo)

        patch = TitleReferencePatchPublic(reference_type="article")

        result = svc.update_title_reference(3, 9, patch, exclude_none=False)

        assert result is updated_ref
        r_repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_title_reference_partial_update(self) -> None:
        """update_title_reference allows partial field updates."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.update.return_value = TitleReferenceReadFactory()
        svc = TitleReferenceService(t_repo, r_repo)

        # Only update reference_url
        patch = TitleReferencePatchPublic(reference_url="https://new-url.com")

        svc.update_title_reference(3, 9, patch, exclude_none=True)

        call_arg = r_repo.update.call_args[0][1]
        assert hasattr(call_arg, "reference_url")
        assert call_arg.title_id == 3

    @pytest.mark.unit
    def test_update_title_reference_not_found(self) -> None:
        """update_title_reference raises 404 when reference doesn't exist."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.update.side_effect = NotFoundError("missing")
        svc = TitleReferenceService(t_repo, r_repo)

        patch = TitleReferencePatchPublic(reference_type="article")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_reference(3, 999, patch, exclude_none=True)

        assert exc_info.value.status_code == 404
        assert "Title Reference not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_title_reference_unique_violation(self) -> None:
        """update_title_reference raises 409 on unique constraint violation."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.update.side_effect = UniqueViolation("u")
        svc = TitleReferenceService(t_repo, r_repo)

        patch = TitleReferencePatchPublic(reference_type="article")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_reference(3, 9, patch, exclude_none=False)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_title_reference_database_locked(self) -> None:
        """update_title_reference raises 423 when database is read-only."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.update.side_effect = DatabaseLocked("locked")
        svc = TitleReferenceService(t_repo, r_repo)

        patch = TitleReferencePatchPublic(reference_type="article")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_reference(3, 9, patch, exclude_none=True)

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
    def test_update_title_reference_constraint_violations(self, exc_class) -> None:
        """update_title_reference raises 422 for various constraint violations."""
        t_repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        r_repo = create_autospec(TitleReferenceRepository, instance=True, spec_set=True)
        r_repo.update.side_effect = exc_class("c")
        svc = TitleReferenceService(t_repo, r_repo)

        patch = TitleReferencePatchPublic(reference_type="article")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title_reference(3, 9, patch, exclude_none=True)

        assert exc_info.value.status_code == 422
