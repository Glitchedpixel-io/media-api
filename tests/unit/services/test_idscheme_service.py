"""Unit tests for IdSchemeService."""

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
from app.repositories.protocols import IdSchemeRepository, ExternalIdentifierRepository
from app.schemas import (
    IdSchemeCreateInternal,
    IdSchemeCreatePublic,
    IdSchemePatchPublic,
    IdSchemeRead,
)
from app.services import IdSchemeService


class TestGetScheme:
    """Tests for IdSchemeService.get_scheme."""

    @pytest.mark.unit
    def test_get_scheme_success(self) -> None:
        """get_scheme returns scheme when found."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        expected_scheme = IdSchemeRead(id=42, code="imdb", label="IMDb", validator=None)
        repo.get.return_value = expected_scheme
        svc = IdSchemeService(repo, external_id_repo)

        result = svc.get_scheme(42)

        assert result is expected_scheme
        assert result.id == 42
        assert result.code == "imdb"
        repo.get.assert_called_once_with(42)

    @pytest.mark.unit
    def test_get_scheme_not_found(self) -> None:
        """get_scheme raises 404 when scheme doesn't exist."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.get.return_value = None
        svc = IdSchemeService(repo, external_id_repo)

        with pytest.raises(HTTPException) as exc_info:
            svc.get_scheme(123)

        assert exc_info.value.status_code == 404
        assert "ID scheme not found" in exc_info.value.detail
        repo.get.assert_called_once_with(123)

    @pytest.mark.unit
    def test_get_scheme_with_different_ids(self) -> None:
        """get_scheme correctly handles different scheme IDs."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        svc = IdSchemeService(repo, external_id_repo)

        test_ids = [1, 50, 999]
        for scheme_id in test_ids:
            repo.reset_mock()
            repo.get.return_value = IdSchemeRead(
                id=scheme_id, code="test", label="Test", validator=None
            )

            result = svc.get_scheme(scheme_id)

            assert result.id == scheme_id
            repo.get.assert_called_once_with(scheme_id)


class TestGetSchemes:
    """Tests for IdSchemeService.get_schemes."""

    @pytest.mark.unit
    def test_get_schemes_success(self) -> None:
        """get_schemes returns list of all schemes."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        schemes = [
            IdSchemeRead(id=1, code="imdb", label="IMDb", validator=None),
            IdSchemeRead(id=2, code="tmdb", label="TMDb", validator=None),
            IdSchemeRead(id=3, code="yt", label="YouTube", validator=None),
        ]
        repo.list_all.return_value = schemes
        svc = IdSchemeService(repo, external_id_repo)

        result = svc.get_schemes()

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].code == "imdb"
        repo.list_all.assert_called_once()

    @pytest.mark.unit
    def test_get_schemes_empty_list(self) -> None:
        """get_schemes returns empty list when no schemes exist."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.list_all.return_value = []
        svc = IdSchemeService(repo, external_id_repo)

        result = svc.get_schemes()

        assert isinstance(result, list)
        assert len(result) == 0
        repo.list_all.assert_called_once()


class TestCreateScheme:
    """Tests for IdSchemeService.create_scheme."""

    @pytest.mark.unit
    def test_create_scheme_success(self) -> None:
        """create_scheme creates new scheme and returns it."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        created_scheme = IdSchemeRead(id=1, code="yt", label="YouTube", validator=None)
        repo.create.return_value = created_scheme
        svc = IdSchemeService(repo, external_id_repo)

        payload = IdSchemeCreatePublic(code="yt", label="YouTube", validator=None)

        result = svc.create_scheme(payload)

        assert result is created_scheme
        assert result.code == "yt"
        assert result.label == "YouTube"

        # Verify internal DTO conversion
        repo.create.assert_called_once()
        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, IdSchemeCreateInternal)
        assert call_arg.code == "yt"
        assert call_arg.label == "YouTube"

    @pytest.mark.unit
    def test_create_scheme_with_validator(self) -> None:
        """create_scheme handles schemes with validator patterns."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        created_scheme = IdSchemeRead(id=1, code="imdb", label="IMDb", validator=r"^tt\d+$")
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.create.return_value = created_scheme
        svc = IdSchemeService(repo, external_id_repo)

        payload = IdSchemeCreatePublic(code="imdb", label="IMDb", validator=r"^tt\d+$")

        result = svc.create_scheme(payload)

        assert result.validator == r"^tt\d+$"
        call_arg = repo.create.call_args[0][0]
        assert call_arg.validator == r"^tt\d+$"

    @pytest.mark.unit
    def test_create_scheme_unique_violation(self) -> None:
        """create_scheme raises 409 on unique constraint violation."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.create.side_effect = UniqueViolation("u")
        svc = IdSchemeService(repo, external_id_repo)

        payload = IdSchemeCreatePublic(code="imdb", label="IMDb", validator=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.create_scheme(payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_scheme_database_locked(self) -> None:
        """create_scheme raises 423 when database is read-only."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.create.side_effect = DatabaseLocked("locked")
        svc = IdSchemeService(repo, external_id_repo)

        payload = IdSchemeCreatePublic(code="imdb", label="IMDb", validator=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.create_scheme(payload)

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
    def test_create_scheme_constraint_violations(self, exc_class) -> None:
        """create_scheme raises 422 for various constraint violations."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.create.side_effect = exc_class("c")
        svc = IdSchemeService(repo, external_id_repo)

        payload = IdSchemeCreatePublic(code="imdb", label="IMDb", validator=None)

        with pytest.raises(HTTPException) as exc_info:
            svc.create_scheme(payload)

        assert exc_info.value.status_code == 422


class TestUpdateScheme:
    """Tests for IdSchemeService.update_scheme."""

    @pytest.mark.unit
    def test_update_scheme_success_with_exclude_none(self) -> None:
        """update_scheme updates scheme with exclude_none=True."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        updated_scheme = IdSchemeRead(id=9, code="imdb", label="Renamed", validator=None)
        repo.update.return_value = updated_scheme
        svc = IdSchemeService(repo, external_id_repo)

        patch = IdSchemePatchPublic(label="Renamed")

        result = svc.update_scheme(9, patch, exclude_none=True)

        assert result is updated_scheme
        assert result.label == "Renamed"
        repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_scheme_success_without_exclude_none(self) -> None:
        """update_scheme updates scheme with exclude_none=False."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        updated_scheme = IdSchemeRead(id=9, code="test", label="X", validator=None)
        repo.update.return_value = updated_scheme
        svc = IdSchemeService(repo, external_id_repo)

        patch = IdSchemePatchPublic(label="X")

        result = svc.update_scheme(9, patch, exclude_none=False)

        assert result is updated_scheme
        repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_scheme_partial_update(self) -> None:
        """update_scheme allows partial field updates."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.update.return_value = IdSchemeRead(id=5, code="test", label="Test", validator=None)
        svc = IdSchemeService(repo, external_id_repo)

        # Only update label
        patch = IdSchemePatchPublic(label="New Label")

        svc.update_scheme(5, patch, exclude_none=True)

        repo.update.assert_called_once()

    @pytest.mark.unit
    def test_update_scheme_not_found(self) -> None:
        """update_scheme raises 404 when scheme doesn't exist."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.update.side_effect = NotFoundError("missing")
        svc = IdSchemeService(repo, external_id_repo)

        patch = IdSchemePatchPublic(label="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_scheme(5, patch, exclude_none=True)

        assert exc_info.value.status_code == 404
        assert "ID scheme not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_scheme_unique_violation(self) -> None:
        """update_scheme raises 409 on unique constraint violation."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.update.side_effect = UniqueViolation("u")
        svc = IdSchemeService(repo, external_id_repo)

        patch = IdSchemePatchPublic(label="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_scheme(5, patch, exclude_none=False)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_scheme_database_locked(self) -> None:
        """update_scheme raises 423 when database is read-only."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.update.side_effect = DatabaseLocked("locked")
        svc = IdSchemeService(repo, external_id_repo)

        patch = IdSchemePatchPublic(label="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_scheme(5, patch, exclude_none=False)

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
    def test_update_scheme_constraint_violations(self, exc_class) -> None:
        """update_scheme raises 422 for various constraint violations."""
        repo = create_autospec(IdSchemeRepository, instance=True, spec_set=True)
        external_id_repo = create_autospec(
            ExternalIdentifierRepository, instance=True, spec_set=True
        )
        repo.update.side_effect = exc_class("c")
        svc = IdSchemeService(repo, external_id_repo)

        patch = IdSchemePatchPublic(label="Y")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_scheme(5, patch, exclude_none=True)

        assert exc_info.value.status_code == 422
