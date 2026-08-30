"""Unit tests for TitleService."""

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
    ArtworkKindRepository,
    ArtworkRepository,
    TitleContentRepository,
    TitleRepository,
    TitleTypeRepository,
)
from app.schemas import (
    ArtworkKindRead,
    PageInfo,
    PaginatedResponse,
    TitleCreateInternal,
    TitleCreatePublic,
    TitleListParams,
    TitlePatchPublic,
    TitleTypeRead,
    TitleUpdateInternal,
)
from app.services import TitleService
from tests.factories import TitleReadFactory


def _artwork_repo(resolved: dict[int, object] | None = None):
    """An artwork repository that resolves display images.

    Defaults to resolving nothing, so tests that are not about artwork do not have to
    care -- `get_title` attaches a display image on every call now.
    """
    repo = create_autospec(ArtworkRepository, instance=True, spec_set=True)
    repo.resolve_for_titles.return_value = resolved or {}
    return repo


def _kind_repo(code: str | None = "poster"):
    """An artwork kind repository carrying one kind, or none when code is None.

    Read through `list_all` rather than per-code lookups since #152: the display image
    walks a chain of kinds, and one call for the lot keeps the query count independent
    of how long that chain grows.
    """
    repo = create_autospec(ArtworkKindRepository, instance=True, spec_set=True)
    repo.list_all.return_value = [ArtworkKindRead(id=7, code=code, label="Poster")] if code else []
    return repo


def _content_repo(counts: dict[int, object] | None = None, totals: dict[int, object] | None = None):
    """A title content repository that aggregates nothing.

    Defaults to empty for both, so tests that are not about aggregates do not have to
    care -- `get_title` attaches counts and totals on every call now, the same way it
    already attaches a poster.
    """
    repo = create_autospec(TitleContentRepository, instance=True, spec_set=True)
    repo.counts_for_titles.return_value = counts or {}
    repo.totals_for_titles.return_value = totals or {}
    return repo


def _type_repo(known: dict[str, int] | None = None):
    """A title type repository that resolves codes to ids.

    Defaults to resolving every code, so tests that are not about type
    resolution do not have to care. Pass an explicit mapping to test the
    unknown-code path.
    """
    repo = create_autospec(TitleTypeRepository, instance=True, spec_set=True)

    def get_by_code(code: str) -> TitleTypeRead | None:
        if known is not None and code not in known:
            return None
        type_id = known[code] if known else 1
        return TitleTypeRead(id=type_id, code=code, label=code)

    repo.get_by_code.side_effect = get_by_code
    return repo


class TestGetTitle:
    """Tests for TitleService.get_title."""

    @pytest.mark.unit
    def test_get_title_success(self) -> None:
        """get_title returns title when found in repository."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        expected_title = TitleReadFactory(id=42, name="Alien", title_type="movie")
        repo.get.return_value = expected_title
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        result = svc.get_title(42)

        # No longer the same object: get_title widens the repository's TitleRead into a
        # TitleReadExtended so it can carry a resolved poster.
        assert result.id == 42
        assert result.name == "Alien"
        assert result.title_type == "movie"
        repo.get.assert_called_once_with(42)

    @pytest.mark.unit
    def test_get_title_not_found(self) -> None:
        """get_title raises 404 when repository returns None."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.get.return_value = None
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        with pytest.raises(HTTPException) as exc_info:
            svc.get_title(123)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail
        repo.get.assert_called_once_with(123)

    @pytest.mark.unit
    def test_get_title_with_various_ids(self) -> None:
        """get_title correctly handles different title IDs."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        test_ids = [1, 500, 999999]
        for title_id in test_ids:
            repo.reset_mock()
            expected = TitleReadFactory(id=title_id)
            repo.get.return_value = expected

            result = svc.get_title(title_id)

            assert result.id == title_id
            repo.get.assert_called_once_with(title_id)

    @pytest.mark.unit
    def test_get_title_with_different_title_types(self) -> None:
        """get_title returns titles with different types correctly."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        for title_type in ["movie", "season", "collection"]:
            repo.reset_mock()
            expected = TitleReadFactory(id=1, title_type=title_type)
            repo.get.return_value = expected

            result = svc.get_title(1)

            assert result.title_type == title_type


class TestGetTitles:
    """Tests for TitleService.get_titles."""

    @pytest.mark.unit
    def test_get_titles_with_default_params(self) -> None:
        """get_titles delegates to repository with provided params."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        titles = [TitleReadFactory() for _ in range(3)]
        expected_response = PaginatedResponse(items=titles, page=PageInfo(next=None, prev=None))
        repo.list_paged.return_value = expected_response
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())
        params = TitleListParams()

        result = svc.get_titles(params)

        assert result is expected_response
        assert len(result.items) == 3
        assert result.page.next is None
        repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_titles_with_pagination(self) -> None:
        """get_titles passes pagination parameters correctly."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next="next_cursor", prev="prev_cursor")
        )
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())
        params = TitleListParams(limit=50, after="cursor123", sort="name:asc")

        result = svc.get_titles(params)

        assert result.page.next == "next_cursor"
        assert result.page.prev == "prev_cursor"
        repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_titles_with_filters(self) -> None:
        """get_titles passes filter parameters to repository."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())
        params = TitleListParams(
            limit=100,
            sort="title_type:desc",
            name="alien",
        )

        result = svc.get_titles(params)

        assert result == repo.list_paged.return_value
        repo.list_paged.assert_called_once_with(params)

    @pytest.mark.unit
    def test_get_titles_empty_result(self) -> None:
        """get_titles returns empty list when no titles match."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        empty_response = PaginatedResponse(items=[], page=PageInfo(next=None, prev=None))
        repo.list_paged.return_value = empty_response
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())
        params = TitleListParams()

        result = svc.get_titles(params)

        assert len(result.items) == 0
        assert isinstance(result.items, list)

    @pytest.mark.unit
    def test_get_titles_with_various_limits(self) -> None:
        """get_titles correctly handles different limit values."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.list_paged.return_value = PaginatedResponse(
            items=[], page=PageInfo(next=None, prev=None)
        )
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        for limit in [10, 50, 100]:
            repo.reset_mock()
            params = TitleListParams(limit=limit)

            svc.get_titles(params)

            call_params = repo.list_paged.call_args[0][0]
            assert call_params.limit == limit


class TestCreateTitle:
    """Tests for TitleService.create_title."""

    @pytest.mark.unit
    def test_create_title_success(self) -> None:
        """create_title creates new title and returns it."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        created_title = TitleReadFactory(id=1, name="The Matrix", title_type="movie")
        repo.create.return_value = created_title
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        payload = TitleCreatePublic(name="The Matrix", title_type="movie")

        result = svc.create_title(payload)

        assert result is created_title
        assert result.id == 1
        assert result.name == "The Matrix"
        assert result.title_type == "movie"

        # Verify internal DTO conversion
        repo.create.assert_called_once()
        call_arg = repo.create.call_args[0][0]
        assert isinstance(call_arg, TitleCreateInternal)
        assert call_arg.name == "The Matrix"
        assert call_arg.title_type_id == 1

    @pytest.mark.unit
    def test_create_title_with_season_type(self) -> None:
        """create_title works with season type titles."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        created_title = TitleReadFactory(name="Season 1", title_type="season")
        repo.create.return_value = created_title
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        payload = TitleCreatePublic(name="Season 1", title_type="season")

        result = svc.create_title(payload)

        assert result.title_type == "season"

    @pytest.mark.unit
    def test_create_title_unique_violation(self) -> None:
        """create_title raises 409 on unique constraint violation."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.create.side_effect = UniqueViolation("Unique constraint")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        payload = TitleCreatePublic(name="Example", title_type="movie")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_title(payload)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_create_title_database_locked(self) -> None:
        """create_title raises 423 when database is read-only."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.create.side_effect = DatabaseLocked("Database locked")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        payload = TitleCreatePublic(name="Example", title_type="movie")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_title(payload)

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
    def test_create_title_constraint_violations(self, exc_class) -> None:
        """create_title raises 422 for various constraint violations."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.create.side_effect = exc_class("Constraint error")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        payload = TitleCreatePublic(name="Example", title_type="movie")

        with pytest.raises(HTTPException) as exc_info:
            svc.create_title(payload)

        assert exc_info.value.status_code == 422


class TestUpdateTitle:
    """Tests for TitleService.update_title."""

    @pytest.mark.unit
    def test_update_title_success_with_exclude_none(self) -> None:
        """update_title updates title with exclude_none=True (PATCH behavior)."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        updated_title = TitleReadFactory(id=9, name="Renamed Title")
        repo.update.return_value = updated_title
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(name="Renamed Title")

        result = svc.update_title(9, patch, exclude_none=True)

        assert result is updated_title
        assert result.id == 9
        assert result.name == "Renamed Title"

        # Verify internal DTO and exclude_none behavior
        repo.update.assert_called_once()
        call_args = repo.update.call_args[0]
        assert call_args[0] == 9
        assert isinstance(call_args[1], TitleUpdateInternal)

    @pytest.mark.unit
    def test_update_title_success_without_exclude_none(self) -> None:
        """update_title updates title with exclude_none=False (PUT behavior)."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        updated_title = TitleReadFactory(id=9, name="X")
        repo.update.return_value = updated_title
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(name="X")

        result = svc.update_title(9, patch, exclude_none=False)

        assert result is updated_title
        repo.update.assert_called_once()
        call_args = repo.update.call_args[0]
        assert call_args[0] == 9
        assert hasattr(call_args[1], "name")

    @pytest.mark.unit
    def test_update_title_partial_update(self) -> None:
        """update_title allows partial field updates."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.update.return_value = TitleReadFactory(id=5)
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        # Only update name, leave other fields unchanged
        patch = TitlePatchPublic(name="Updated Name")

        svc.update_title(5, patch, exclude_none=True)

        repo.update.assert_called_once()
        call_arg = repo.update.call_args[0][1]
        assert hasattr(call_arg, "name")

    @pytest.mark.unit
    def test_update_title_change_title_type(self) -> None:
        """update_title allows changing title type."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        updated = TitleReadFactory(id=5, title_type="season")
        repo.update.return_value = updated
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(title_type="season")

        result = svc.update_title(5, patch, exclude_none=True)

        assert result.title_type == "season"

    @pytest.mark.unit
    def test_update_title_not_found(self) -> None:
        """update_title raises 404 when title doesn't exist."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.update.side_effect = NotFoundError("Title not found")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(name="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title(5, patch, exclude_none=True)

        assert exc_info.value.status_code == 404
        assert "Title not found" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_title_unique_violation(self) -> None:
        """update_title raises 409 on unique constraint violation."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.update.side_effect = UniqueViolation("Unique constraint")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(name="X")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title(5, patch, exclude_none=False)

        assert exc_info.value.status_code == 409
        assert "Unique constraint violated" in exc_info.value.detail

    @pytest.mark.unit
    def test_update_title_database_locked(self) -> None:
        """update_title raises 423 when database is read-only."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.update.side_effect = DatabaseLocked("Database locked")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(name="Y")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title(5, patch, exclude_none=True)

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
    def test_update_title_constraint_violations(self, exc_class) -> None:
        """update_title raises 422 for various constraint violations."""
        repo = create_autospec(TitleRepository, instance=True, spec_set=True)
        repo.update.side_effect = exc_class("Constraint error")
        svc = TitleService(repo, _type_repo(), _artwork_repo(), _kind_repo(), _content_repo())

        patch = TitlePatchPublic(name="Y")

        with pytest.raises(HTTPException) as exc_info:
            svc.update_title(5, patch, exclude_none=True)

        assert exc_info.value.status_code == 422
