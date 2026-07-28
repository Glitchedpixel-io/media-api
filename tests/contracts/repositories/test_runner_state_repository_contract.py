# tests/contracts/repositories/test_runner_state_repository_contract.py
from __future__ import annotations

import pytest

from app.repositories.errors import NotFoundError
from app.schemas import (
    RunnerStateCreateInternal,
    RunnerStateUpdateInternal,
)
from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    runner_state_bundler,
)


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, runner_state_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_and_get(bundle):

    created = bundle.runner_state.create(
        RunnerStateCreateInternal(runner_key="youtube", state={"page": 1})
    )
    assert created.runner_key == "youtube"
    assert created.updated_at is not None
    assert created.state == {"page": 1}

    fetched = bundle.runner_state.get_runner_state("youtube")
    assert fetched is not None
    assert fetched.runner_key == "youtube"
    assert fetched.state == {"page": 1}


@pytest.mark.contract
def test_update_state_and_not_found(bundle):

    bundle.runner_state.create(
        RunnerStateCreateInternal(runner_key="scanner", state={"offset": 10})
    )

    updated = bundle.runner_state.set_runner_state(
        "scanner", RunnerStateUpdateInternal(state={"offset": 25})
    )
    assert updated.state == {"offset": 25}

    # updating with partial keeps other fields intact (no other mutable fields currently)
    updated2 = bundle.runner_state.set_runner_state(
        "scanner", RunnerStateUpdateInternal(state=None)
    )
    assert updated2.state is None

    with pytest.raises(NotFoundError):
        bundle.runner_state.set_runner_state(
            "does-not-exist", RunnerStateUpdateInternal(state={"a": 1})
        )
