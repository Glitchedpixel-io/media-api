# tests/contracts/repositories/test_job_repository_contract.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.repositories.errors import NotFoundError
from app.schemas import JobRead
from tests.contracts.repositories.bundles_impl import make_bundle, job_bundler


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, job_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists(bundle):
    created = bundle.jobs.create(job_key="job-1")
    assert isinstance(created, JobRead)
    assert created.job_key == "job-1"
    assert created.created_at is not None
    assert created.heartbeat_at is None
    assert created.completed_at is None

    got = bundle.jobs.get("job-1")
    assert got is not None
    assert got.job_key == "job-1"

    assert bundle.jobs.exists("job-1") is True
    assert bundle.jobs.exists("missing") is False


@pytest.mark.contract
def test_heartbeat_updates_timestamp(bundle):
    bundle.jobs.create(job_key="job-2")

    before = datetime.now(UTC)
    updated = bundle.jobs.heartbeat("job-2")

    assert updated.heartbeat_at is not None
    assert updated.heartbeat_at >= before


@pytest.mark.contract
def test_cannot_heartbeat_after_completed(bundle):
    bundle.jobs.create(job_key="job-3")
    bundle.jobs.mark_complete("job-3")

    with pytest.raises(ValueError):
        bundle.jobs.heartbeat("job-3")


@pytest.mark.contract
def test_mark_complete_sets_completed_at(bundle):
    bundle.jobs.create(job_key="job-4")

    updated = bundle.jobs.mark_complete("job-4")

    assert updated.completed_at is not None


@pytest.mark.contract
@pytest.mark.parametrize("method", ["heartbeat", "mark_complete", "get"])
def test_not_found_errors(bundle, method: str):
    if method == "get":
        assert bundle.jobs.get("missing") is None
    else:
        with pytest.raises(NotFoundError):
            getattr(bundle.jobs, method)("missing")
