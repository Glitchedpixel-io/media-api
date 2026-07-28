"""Unit tests for the default NullJobRunner (pure pull model)."""

from __future__ import annotations

import pytest

from app.runners import JobDispatch, NullJobRunner


class TestNullJobRunner:
    @pytest.mark.unit
    def test_dispatch_is_noop(self) -> None:
        runner = NullJobRunner()

        result = runner.dispatch(JobDispatch(job_id=1, job_type="transcode"))

        assert result is None

    @pytest.mark.unit
    def test_fetch_logs_returns_empty(self) -> None:
        runner = NullJobRunner()

        assert runner.fetch_logs("any-ref") == []
