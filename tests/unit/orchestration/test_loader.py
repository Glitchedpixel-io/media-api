"""Unit tests for build_provider_registry (entry-point discovery and enabling)."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from app.config.schema import OrchestrationConfig
from app.orchestration.loader import ENTRY_POINT_GROUP, build_provider_registry
from app.orchestration.providers import PROVIDER_API_VERSION


class _FakeProvider:
    def __init__(self, url: str = "unused") -> None:
        self.url = url

    key = "fake"
    api_version = PROVIDER_API_VERSION

    def dispatch(self, route, job) -> None: ...

    def fetch_logs(self, route, external_job_id: str) -> list:
        return []


class _WrongVersionProvider:
    key = "fake"
    api_version = PROVIDER_API_VERSION + 1

    def dispatch(self, route, job) -> None: ...

    def fetch_logs(self, route, external_job_id: str) -> list:
        return []


class _OtherFakeProvider:
    """A distinct class that happens to declare the same provider key as _FakeProvider."""

    key = "fake"
    api_version = PROVIDER_API_VERSION

    def dispatch(self, route, job) -> None: ...

    def fetch_logs(self, route, external_job_id: str) -> list:
        return []


def _fake_entry_point(name: str, obj: type) -> EntryPoint:
    """Build a real EntryPoint whose .load() returns `obj`, bypassing dist metadata."""
    ep = EntryPoint(
        name=name,
        value=f"{obj.__module__}:{obj.__qualname__}",
        group=ENTRY_POINT_GROUP,
    )
    # EntryPoint.load() resolves via value; since these fake classes are
    # module-level in this test file, module:qualname resolution works as-is.
    return ep


@pytest.fixture
def discovered(monkeypatch: pytest.MonkeyPatch):
    def _install(mapping: dict[str, type]) -> None:
        entry_points = tuple(_fake_entry_point(name, obj) for name, obj in mapping.items())
        monkeypatch.setattr(
            "app.orchestration.loader.entry_points",
            lambda group: entry_points if group == ENTRY_POINT_GROUP else (),
        )

    return _install


class TestBuildProviderRegistry:
    @pytest.mark.unit
    def test_no_enabled_providers_returns_empty_registry(self, discovered) -> None:
        discovered({"fake": _FakeProvider})

        registry = build_provider_registry(OrchestrationConfig())

        # No provider was enabled, so dispatch is a no-op regardless of what's installed.
        from app.runners.protocols import JobDispatch

        registry.dispatch(JobDispatch(job_id=1, job_type="fake.thing"))

    @pytest.mark.unit
    def test_enabled_and_discovered_provider_is_instantiated(self, discovered) -> None:
        discovered({"fake": _FakeProvider})

        registry = build_provider_registry(OrchestrationConfig(enabled_providers=("fake",)))

        from app.runners.protocols import JobDispatch

        job = JobDispatch(job_id=1, job_type="fake.thing")
        # No exception means the fake provider is registered and dispatch resolves.
        registry.dispatch(job)

    @pytest.mark.unit
    def test_enabled_but_not_discovered_raises_clear_error(self, discovered) -> None:
        discovered({})

        with pytest.raises(ValueError, match="no entry point is registered"):
            build_provider_registry(OrchestrationConfig(enabled_providers=("fake",)))

    @pytest.mark.unit
    def test_provider_options_are_passed_as_constructor_kwargs(self, discovered) -> None:
        discovered({"fake": _FakeProvider})

        config = OrchestrationConfig(
            enabled_providers=("fake",),
            provider_options={"fake": {"url": "https://example.com"}},
        )

        # Must not raise -- confirms kwargs reached the constructor.
        build_provider_registry(config)

    @pytest.mark.unit
    def test_bad_provider_options_raise_clear_error(self, discovered) -> None:
        discovered({"fake": _FakeProvider})

        config = OrchestrationConfig(
            enabled_providers=("fake",),
            provider_options={"fake": {"unexpected_kwarg": "x"}},
        )

        with pytest.raises(ValueError, match="Failed to construct orchestration provider"):
            build_provider_registry(config)

    @pytest.mark.unit
    def test_incompatible_api_version_raises_clear_error(self, discovered) -> None:
        discovered({"fake": _WrongVersionProvider})

        with pytest.raises(ValueError, match="api_version"):
            build_provider_registry(OrchestrationConfig(enabled_providers=("fake",)))

    @pytest.mark.unit
    def test_duplicate_provider_keys_across_entry_points_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two distinct entry-point *names* both resolving to provider key "fake".
        entry_points = (
            _fake_entry_point("fake", _FakeProvider),
            _fake_entry_point("fake-alias", _OtherFakeProvider),
        )
        monkeypatch.setattr(
            "app.orchestration.loader.entry_points",
            lambda group: entry_points if group == ENTRY_POINT_GROUP else (),
        )

        with pytest.raises(ValueError, match="Duplicate orchestration provider"):
            build_provider_registry(OrchestrationConfig(enabled_providers=("fake", "fake-alias")))
