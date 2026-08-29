# tests/contracts/repositories/test_title_type_repository_contract.py
from __future__ import annotations

import pytest

from app.models import DEFAULT_TITLE_TYPES
from app.repositories.errors import NotFoundError, UniqueViolation
from app.repositories.title_type_repository import SQLAlchemyTitleTypeRepository
from app.schemas import TitleTypeCreateInternal, TitleTypeUpdateInternal
from tests.factories import TitleCreateFactory


@pytest.fixture
def repo(db_session):
    return SQLAlchemyTitleTypeRepository(db_session)


@pytest.mark.contract
def test_seeded_types_are_listed_alphabetically_by_code(repo):
    types = repo.list_all()
    assert len(types) == len(DEFAULT_TITLE_TYPES)
    codes = [t.code for t in types]
    assert codes == sorted(codes)
    assert set(codes) == {code for code, _ in DEFAULT_TITLE_TYPES}


@pytest.mark.contract
def test_create_get_exists_roundtrip(repo):
    created = repo.create(TitleTypeCreateInternal(code="podcast", label="Podcast"))
    assert created.id is not None
    assert repo.exists(created.id) is True

    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.code == "podcast"
    assert fetched.label == "Podcast"


@pytest.mark.contract
def test_get_by_code(repo):
    assert repo.get_by_code("movie") is not None
    assert repo.get_by_code("movie").code == "movie"
    assert repo.get_by_code("nope") is None


@pytest.mark.contract
def test_get_and_exists_for_missing_id(repo):
    assert repo.get(9999) is None
    assert repo.exists(9999) is False


@pytest.mark.contract
def test_duplicate_code_raises_unique_violation(repo):
    with pytest.raises(UniqueViolation):
        repo.create(TitleTypeCreateInternal(code="movie", label="Movie Again"))


@pytest.mark.contract
def test_update_only_touches_supplied_fields(repo):
    movie = repo.get_by_code("movie")
    updated = repo.update(
        movie.id, TitleTypeUpdateInternal.model_validate({"label": "Feature Film"})
    )
    assert updated.label == "Feature Film"
    assert updated.code == "movie"


@pytest.mark.contract
def test_update_missing_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.update(9999, TitleTypeUpdateInternal.model_validate({"label": "x"}))


@pytest.mark.contract
def test_delete_removes_an_unused_type(repo):
    created = repo.create(TitleTypeCreateInternal(code="podcast", label="Podcast"))
    repo.delete(created.id)
    assert repo.get(created.id) is None


@pytest.mark.contract
def test_delete_missing_raises_not_found(repo):
    with pytest.raises(NotFoundError):
        repo.delete(9999)


@pytest.mark.contract
def test_usage_count_reflects_titles_using_the_type(repo, db_session):
    from app.repositories.title_repository import SQLAlchemyTitleRepository

    movie = repo.get_by_code("movie")
    assert repo.usage_count(movie.id) == 0

    titles = SQLAlchemyTitleRepository(db_session)
    titles.create(TitleCreateFactory(name="Alien", title_type_id=movie.id))
    titles.create(TitleCreateFactory(name="Dune", title_type_id=movie.id))

    assert repo.usage_count(movie.id) == 2
    assert repo.usage_count(repo.get_by_code("episode").id) == 0
