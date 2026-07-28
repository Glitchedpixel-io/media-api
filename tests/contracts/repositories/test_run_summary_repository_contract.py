# tests/contracts/repositories/test_run_summary_repository_contract.py
import pytest

from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    run_summary_bundler,
)
from tests.factories import RunSummaryFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, run_summary_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):
    rs = RunSummaryFactory()
    out = bundle.run_summary.create(rs)
    assert out.id is not None
    assert bundle.run_summary.exists(out.id) is True
    fetched = bundle.run_summary.get(out.id)
    assert fetched is not None
    assert fetched.created_at is not None
