"""Unit tests for the Prefect job-runner adapter.

Every ``prefect`` symbol is imported lazily inside the adapter, so the tests
patch those lazy import targets rather than importing Prefect eagerly.
"""

from __future__ import annotations

import pytest

from app.runners import JobDispatch, LogEntry
from app.runners.facade import CompositeJobRunner
from app.runners.prefect_runner import (
    PrefectDispatcher,
    PrefectJobRunner,
    PrefectLogSource,
    _fetch_flow_run_logs,
)


class TestPrefectDispatcher:
    @pytest.mark.unit
    def test_dispatch_unknown_job_type_is_noop(self, mocker) -> None:
        run_deployment = mocker.patch("prefect.deployments.run_deployment")

        dispatcher = PrefectDispatcher({"transcode": "flow/Deployment"})
        result = dispatcher.dispatch(JobDispatch(job_id=1, job_type="unmapped"))

        assert result is None
        run_deployment.assert_not_called()

    @pytest.mark.unit
    def test_dispatch_triggers_deployment(self, mocker) -> None:
        run_deployment = mocker.patch("prefect.deployments.run_deployment")

        dispatcher = PrefectDispatcher({"transcode": "flow/Deployment"})
        result = dispatcher.dispatch(JobDispatch(job_id=1, job_type="transcode"))

        assert result is None
        run_deployment.assert_called_once_with(name="flow/Deployment", timeout=0)

    @pytest.mark.unit
    def test_dispatch_swallows_errors(self, mocker) -> None:
        mocker.patch(
            "prefect.deployments.run_deployment",
            side_effect=RuntimeError("prefect down"),
        )

        dispatcher = PrefectDispatcher({"transcode": "flow/Deployment"})

        assert dispatcher.dispatch(JobDispatch(job_id=1, job_type="transcode")) is None


class TestPrefectLogSource:
    @pytest.mark.unit
    def test_fetch_logs_delegates_to_helper(self, mocker) -> None:
        entries = [
            LogEntry(
                timestamp="2024-01-01",
                level="INFO",
                logger="prefect",
                message="hi",
                external_ref="ref",
            )
        ]
        helper = mocker.patch(
            "app.runners.prefect_runner._fetch_flow_run_logs", return_value=entries
        )

        source = PrefectLogSource(limit=25)
        result = source.fetch_logs("ref-1")

        assert result == entries
        helper.assert_called_once_with("ref-1", limit=25)


class TestPrefectJobRunner:
    @pytest.mark.unit
    def test_runner_wires_dispatcher_and_log_source(self) -> None:
        runner = PrefectJobRunner({"transcode": "flow/Deployment"})

        assert isinstance(runner, CompositeJobRunner)
        assert isinstance(runner._dispatcher, PrefectDispatcher)
        assert isinstance(runner._log_source, PrefectLogSource)


class _FakeLevel:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeLog:
    def __init__(self, level, name, message, flow_run_id) -> None:
        self.timestamp = "2024-01-01T00:00:00"
        self.level = level
        self.name = name
        self.message = message
        self.flow_run_id = flow_run_id


class _FakeClient:
    def __init__(self, batches) -> None:
        self._batches = batches
        self._call = 0
        self.calls: list[dict] = []

    async def read_logs(self, log_filter, limit, offset, sort):  # noqa: ANN001
        self.calls.append({"limit": limit, "offset": offset})
        if self._call < len(self._batches):
            batch = self._batches[self._call]
            self._call += 1
            return batch
        return []


class _FakeClientCtx:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def __aenter__(self) -> _FakeClient:
        return self._client

    async def __aexit__(self, *exc) -> bool:  # noqa: ANN002
        return False


FLOW_RUN_ID = "123e4567-e89b-12d3-a456-426614174000"


class TestFetchFlowRunLogs:
    @pytest.mark.unit
    def test_fetch_maps_and_pages_logs(self, mocker) -> None:
        first_batch = [
            _FakeLog(_FakeLevel("INFO"), "prefect.flow", "started", FLOW_RUN_ID),
            _FakeLog(20, None, "int-level", None),
        ]
        client = _FakeClient([first_batch])
        mocker.patch("prefect.get_client", return_value=_FakeClientCtx(client))

        result = _fetch_flow_run_logs(FLOW_RUN_ID, limit=50)

        assert result == [
            LogEntry(
                timestamp="2024-01-01T00:00:00",
                level="INFO",
                logger="prefect.flow",
                message="started",
                external_ref=FLOW_RUN_ID,
            ),
            LogEntry(
                timestamp="2024-01-01T00:00:00",
                level="20",
                logger=None,
                message="int-level",
                external_ref=None,
            ),
        ]
        # First page fetched at offset 0, then a second (empty) page to stop.
        assert client.calls[0] == {"limit": 50, "offset": 0}
        assert client.calls[1] == {"limit": 50, "offset": 2}

    @pytest.mark.unit
    def test_fetch_returns_empty_when_no_logs(self, mocker) -> None:
        client = _FakeClient([])
        mocker.patch("prefect.get_client", return_value=_FakeClientCtx(client))

        assert _fetch_flow_run_logs(FLOW_RUN_ID) == []
