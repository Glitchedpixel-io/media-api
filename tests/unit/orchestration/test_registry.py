"""Unit tests for ProviderRegistry."""

from __future__ import annotations

import pytest

import app.orchestration.registry as registry_module
from app.orchestration.registry import (
    ProviderRegistry,
    get_provider_registry,
    init_provider_registry,
)
from app.runners.protocols import JobDispatch, LogEntry


@pytest.fixture(autouse=True)
def _reset_global_registry():
    """The module-level singleton must not leak state between tests."""
    original = registry_module._provider_registry
    yield
    registry_module._provider_registry = original


class _FakeProvider:
    def __init__(self, key: str, *, logs: list[LogEntry] | None = None) -> None:
        self.key = key
        self.api_version = 1
        self._logs = logs or []
        self.dispatched: list[tuple] = []
        self.fetched: list[tuple] = []

    def dispatch(self, route, job: JobDispatch) -> None:
        self.dispatched.append((route, job))

    def fetch_logs(self, route, external_job_id: str) -> list[LogEntry]:
        self.fetched.append((route, external_job_id))
        return list(self._logs)


class TestProviderRegistryConstruction:
    @pytest.mark.unit
    def test_rejects_duplicate_provider_keys(self) -> None:
        with pytest.raises(ValueError, match="Duplicate orchestration provider"):
            ProviderRegistry([_FakeProvider("prefect"), _FakeProvider("prefect")])

    @pytest.mark.unit
    def test_duplicate_check_is_case_insensitive(self) -> None:
        with pytest.raises(ValueError, match="Duplicate orchestration provider"):
            ProviderRegistry([_FakeProvider("Prefect"), _FakeProvider("prefect")])

    @pytest.mark.unit
    def test_accepts_multiple_distinct_providers(self) -> None:
        registry = ProviderRegistry([_FakeProvider("prefect"), _FakeProvider("webhook")])

        assert registry is not None


class TestProviderRegistryDispatch:
    @pytest.mark.unit
    def test_dispatch_routes_to_matching_provider(self) -> None:
        prefect = _FakeProvider("prefect")
        webhook = _FakeProvider("webhook")
        registry = ProviderRegistry([prefect, webhook])
        job = JobDispatch(job_id=1, job_type="prefect.transcode")

        registry.dispatch(job)

        assert len(prefect.dispatched) == 1
        assert prefect.dispatched[0][1] is job
        assert webhook.dispatched == []

    @pytest.mark.unit
    def test_dispatch_is_case_insensitive_on_provider(self) -> None:
        prefect = _FakeProvider("prefect")
        registry = ProviderRegistry([prefect])

        registry.dispatch(JobDispatch(job_id=1, job_type="PREFECT.transcode"))

        assert len(prefect.dispatched) == 1

    @pytest.mark.unit
    def test_dispatch_unknown_provider_is_noop(self) -> None:
        registry = ProviderRegistry([_FakeProvider("prefect")])

        # Must not raise.
        registry.dispatch(JobDispatch(job_id=1, job_type="temporal.transcode"))

    @pytest.mark.unit
    def test_dispatch_unparseable_transform_type_is_noop(self) -> None:
        registry = ProviderRegistry([_FakeProvider("prefect")])

        # Must not raise.
        registry.dispatch(JobDispatch(job_id=1, job_type="no-dot-here"))

    @pytest.mark.unit
    def test_dispatch_on_empty_registry_is_noop(self) -> None:
        registry = ProviderRegistry([])

        registry.dispatch(JobDispatch(job_id=1, job_type="prefect.transcode"))


class TestProviderRegistryFetchLogs:
    @pytest.mark.unit
    def test_fetch_logs_routes_to_matching_provider(self) -> None:
        entry = LogEntry(
            timestamp="2024-01-01",
            level="INFO",
            logger="prefect",
            message="hi",
            external_ref="job-1",
        )
        prefect = _FakeProvider("prefect", logs=[entry])
        registry = ProviderRegistry([prefect])

        result = registry.fetch_logs("prefect.transcode", "job-1")

        assert result == [entry]
        assert prefect.fetched == [(prefect.fetched[0][0], "job-1")]

    @pytest.mark.unit
    def test_fetch_logs_unknown_provider_returns_empty(self) -> None:
        registry = ProviderRegistry([_FakeProvider("prefect")])

        assert registry.fetch_logs("temporal.transcode", "job-1") == []

    @pytest.mark.unit
    def test_fetch_logs_unparseable_transform_type_returns_empty(self) -> None:
        registry = ProviderRegistry([_FakeProvider("prefect")])

        assert registry.fetch_logs("no-dot-here", "job-1") == []

    @pytest.mark.unit
    def test_fetch_logs_on_empty_registry_returns_empty(self) -> None:
        registry = ProviderRegistry([])

        assert registry.fetch_logs("prefect.transcode", "job-1") == []


class TestProviderRegistrySingleton:
    @pytest.mark.unit
    def test_get_before_init_raises(self) -> None:
        registry_module._provider_registry = None

        with pytest.raises(RuntimeError, match="not initialized"):
            get_provider_registry()

    @pytest.mark.unit
    def test_init_then_get_returns_same_instance(self) -> None:
        registry = ProviderRegistry([])

        result = init_provider_registry(registry)

        assert result is registry
        assert get_provider_registry() is registry
