"""Unit tests for the CompositeJobRunner facade."""

from __future__ import annotations

import pytest

from app.runners import CompositeJobRunner, JobDispatch, LogEntry


class _RecordingDispatcher:
    def __init__(self, return_value: str | None = None) -> None:
        self.return_value = return_value
        self.calls: list[JobDispatch] = []

    def dispatch(self, job: JobDispatch) -> str | None:
        self.calls.append(job)
        return self.return_value


class _RecordingLogSource:
    def __init__(self, entries: list[LogEntry]) -> None:
        self.entries = entries
        self.calls: list[str] = []

    def fetch_logs(self, external_ref: str) -> list[LogEntry]:
        self.calls.append(external_ref)
        return self.entries


@pytest.fixture
def job() -> JobDispatch:
    return JobDispatch(job_id=1, job_type="transcode", parameters={"a": 1})


class TestCompositeDispatch:
    @pytest.mark.unit
    def test_dispatch_without_dispatcher_is_noop(self, job) -> None:
        runner = CompositeJobRunner()

        assert runner.dispatch(job) is None

    @pytest.mark.unit
    def test_dispatch_delegates_to_dispatcher(self, job) -> None:
        dispatcher = _RecordingDispatcher(return_value="ref-123")
        runner = CompositeJobRunner(dispatcher=dispatcher)

        result = runner.dispatch(job)

        assert result == "ref-123"
        assert dispatcher.calls == [job]


class TestCompositeFetchLogs:
    @pytest.mark.unit
    def test_fetch_logs_without_log_source_returns_empty(self) -> None:
        runner = CompositeJobRunner()

        assert runner.fetch_logs("ref-1") == []

    @pytest.mark.unit
    def test_fetch_logs_delegates_to_log_source(self) -> None:
        entry = LogEntry(
            timestamp="2024-01-01",
            level="INFO",
            logger="prefect",
            message="hello",
            external_ref="ref-1",
        )
        log_source = _RecordingLogSource([entry])
        runner = CompositeJobRunner(log_source=log_source)

        result = runner.fetch_logs("ref-1")

        assert result == [entry]
        assert log_source.calls == ["ref-1"]
