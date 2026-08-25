# tests/unit/services/test_title_type_service.py
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.repositories.errors import NotFoundError, UniqueViolation
from app.schemas import (
    TitleTypeCreatePublic,
    TitleTypePatchPublic,
    TitleTypeRead,
)
from app.services import TitleTypeService


@pytest.fixture
def repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(repo: MagicMock) -> TitleTypeService:
    return TitleTypeService(repo)


def _read(**kwargs) -> TitleTypeRead:
    return TitleTypeRead.model_validate({"id": 1, "code": "movie", "label": "Movie", **kwargs})


@pytest.mark.unit
class TestGetTitleTypes:
    def test_returns_repository_list(self, service, repo):
        repo.list_all.return_value = [_read()]
        assert service.get_title_types() == [_read()]

    def test_get_one_returns_it(self, service, repo):
        repo.get.return_value = _read()
        assert service.get_title_type(1).code == "movie"

    def test_get_missing_raises_404(self, service, repo):
        repo.get.return_value = None
        with pytest.raises(HTTPException) as exc:
            service.get_title_type(42)
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestCreateTitleType:
    def test_creates_and_returns(self, service, repo):
        repo.create.return_value = _read(code="podcast", label="Podcast")
        result = service.create_title_type(TitleTypeCreatePublic(code="podcast", label="Podcast"))
        assert result.code == "podcast"
        assert repo.create.called

    def test_duplicate_code_becomes_409(self, service, repo):
        repo.create.side_effect = UniqueViolation("duplicate")
        with pytest.raises(HTTPException) as exc:
            service.create_title_type(TitleTypeCreatePublic(code="movie", label="Movie"))
        assert exc.value.status_code == 409


@pytest.mark.unit
class TestUpdateTitleType:
    def test_updates(self, service, repo):
        repo.update.return_value = _read(label="Feature Film")
        result = service.update_title_type(1, TitleTypePatchPublic(label="Feature Film"), True)
        assert result.label == "Feature Film"

    def test_missing_becomes_404(self, service, repo):
        repo.update.side_effect = NotFoundError
        with pytest.raises(HTTPException) as exc:
            service.update_title_type(42, TitleTypePatchPublic(label="x"), True)
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestDeleteTitleType:
    def test_deletes_an_unused_type(self, service, repo):
        repo.exists.return_value = True
        repo.usage_count.return_value = 0
        service.delete_title_type(1)
        repo.delete.assert_called_once_with(1)

    def test_missing_raises_404(self, service, repo):
        repo.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.delete_title_type(42)
        assert exc.value.status_code == 404
        assert not repo.delete.called

    def test_type_in_use_raises_409_not_422(self, service, repo):
        """A referenced type is a conflict, not a validation failure.

        The ondelete="RESTRICT" foreign key would stop the delete regardless,
        but it surfaces as a ForeignKeyViolation, which
        translate_repository_errors maps to 422. The usage check exists to give
        the caller the right status and a message naming the count.
        """
        repo.exists.return_value = True
        repo.usage_count.return_value = 3
        with pytest.raises(HTTPException) as exc:
            service.delete_title_type(1)
        assert exc.value.status_code == 409
        assert "3" in exc.value.detail
        assert not repo.delete.called
