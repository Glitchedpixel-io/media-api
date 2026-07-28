# tests/contracts/repositories/test_idscheme_repository_contract.py
from __future__ import annotations

import pytest

from app.repositories.errors import UniqueViolation, NotFoundError, ForeignKeyViolation
from app.schemas import IdSchemeUpdateInternal, AssetIdCreateInternal, AssetIdUpdateInternal
from tests.contracts.repositories.bundles_impl import make_bundle
from tests.contracts.repositories.bundles_impl import (
    _sqlite_engine,
    _sqlite_session,
)
from tests.contracts.repositories.bundles_impl import idscheme_bundler
from tests.contracts.repositories._bundles import IdSchemeRepoBundle
from tests.factories import IdSchemeCreateFactory, AssetCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine) -> IdSchemeRepoBundle:
    b = make_bundle(db_session, _test_engine, idscheme_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle: IdSchemeRepoBundle):
    created = bundle.id_schemes.create(IdSchemeCreateFactory())
    assert created.id is not None
    assert created.code is not None
    assert created.label is not None

    assert bundle.id_schemes.exists(created.id) is True

    fetched = bundle.id_schemes.get(created.id)
    assert fetched is not None
    assert fetched.code == created.code
    assert fetched.label == created.label

    by_code = bundle.id_schemes.get_by_code(created.code)
    assert by_code is not None
    assert by_code.id == created.id


@pytest.mark.contract
def test_create_duplicate_code(bundle: IdSchemeRepoBundle):
    first = bundle.id_schemes.create(IdSchemeCreateFactory(code="imdb"))
    assert first is not None
    with pytest.raises(UniqueViolation):
        bundle.id_schemes.create(IdSchemeCreateFactory(code="imdb"))


@pytest.mark.contract
def test_list_all_orders_by_code(bundle: IdSchemeRepoBundle):
    codes = ["zzz", "aaa", "mmm"]
    for c in codes:
        bundle.id_schemes.create(IdSchemeCreateFactory(code=c))

    all_ = bundle.id_schemes.list_all()
    assert [s.code for s in all_] == sorted(codes)


@pytest.mark.contract
def test_update_fields(bundle: IdSchemeRepoBundle):
    s = bundle.id_schemes.create(IdSchemeCreateFactory(code="yt", label="YouTube", validator=None))
    updated = bundle.id_schemes.update(
        s.id,
        IdSchemeUpdateInternal(label="YouTube IDs", validator="^[A-Za-z0-9_-]{11}$"),  # type: ignore
    )
    assert updated.label == "YouTube IDs"
    assert updated.validator == "^[A-Za-z0-9_-]{11}$"


@pytest.mark.contract
def test_update_trivial(bundle: IdSchemeRepoBundle):
    s = bundle.id_schemes.create(IdSchemeCreateFactory())
    updated = bundle.id_schemes.update(s.id, IdSchemeUpdateInternal())  # type: ignore
    assert updated.id == s.id
    assert updated.code == s.code
    assert updated.label == s.label
    assert updated.validator == s.validator
