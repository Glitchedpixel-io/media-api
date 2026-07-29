"""Unit tests for the built-in Prefect orchestration provider.

Every ``prefect`` symbol is imported lazily inside the adapter, so the tests
patch those lazy import targets rather than importing Prefect eagerly.
"""

from __future__ import annotations

import pytest

from app.orchestration.prefect import PrefectProvider, _fetch_flow_run_logs
from app.orchestration.providers import TransformRoute
from app.runners.protocols import JobDispatch, LogEntry


class TestPrefectProviderConstruction:
    @pytest.mark.unit
    def test_raises_clearly_when_prefect_not_installed(self, mocker) -> None:
        mocker.patch("importlib.util.find_spec", return_value=None)

        with pytest.raises(RuntimeError, match="media-api\\[prefect\\]"):
            PrefectProvider()

    @pytest.mark.unit
    def test_constructs_when_prefect_is_installed(self) -> None:
        # The real `prefect` package is a dev dependency in this repo (see
        # pyproject.toml), so find_spec succeeds without mocking.
        provider = PrefectProvider()

        assert provider.key == "prefect"


class TestPrefectProviderDispatch:
    @pytest.mark.unit
    def test_dispatch_runs_deployment_named_by_route_command(self, mocker) -> None:
        run_deployment = mocker.patch("prefect.deployments.run_deployment")
        provider = PrefectProvider()

        provider.dispatch(
            TransformRoute(provider="prefect", command="transcode"),
            JobDispatch(job_id=1, job_type="prefect.transcode"),
        )

        run_deployment.assert_called_once_with(name="transcode", timeout=0)

    @pytest.mark.unit
    def test_dispatch_preserves_dots_in_command(self, mocker) -> None:
        run_deployment = mocker.patch("prefect.deployments.run_deployment")
        provider = PrefectProvider()

        provider.dispatch(
            TransformRoute(provider="prefect", command="deploy.v2"),
            JobDispatch(job_id=1, job_type="prefect.deploy.v2"),
        )

        run_deployment.assert_called_once_with(name="deploy.v2", timeout=0)

    @pytest.mark.unit
    def test_dispatch_swallows_errors(self, mocker) -> None:
        mocker.patch(
            "prefect.deployments.run_deployment",
            side_effect=RuntimeError("prefect down"),
        )
        provider = PrefectProvider()

        # Must not raise.
        provider.dispatch(
            TransformRoute(provider="prefect", command="transcode"),
            JobDispatch(job_id=1, job_type="prefect.transcode"),
        )


class TestPrefectProviderFetchLogs:
    @pytest.mark.unit
    def test_fetch_logs_delegates_to_helper_with_configured_limit(self, mocker) -> None:
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
            "app.orchestration.prefect._fetch_flow_run_logs", return_value=entries
        )
        provider = PrefectProvider(log_limit=25)

        result = provider.fetch_logs(TransformRoute(provider="prefect", command="x"), "ref-1")

        assert result == entries
        helper.assert_called_once_with("ref-1", limit=25)


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
