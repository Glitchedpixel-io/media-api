"""Unit tests for build_job_runner (backend selection)."""

from __future__ import annotations

import pytest

from app.config.schema import RunnerConfig
from app.runners import CompositeJobRunner, NullJobRunner, build_job_runner
from app.runners.factory import build_job_runner as build_job_runner_direct
from app.runners.prefect_runner import PrefectJobRunner
from app.runners.webhook_runner import WebhookDispatcher


class TestBuildJobRunner:
    @pytest.mark.unit
    def test_default_backend_returns_null_runner(self) -> None:
        runner = build_job_runner(RunnerConfig())

        assert isinstance(runner, NullJobRunner)

    @pytest.mark.unit
    def test_explicit_none_backend_returns_null_runner(self) -> None:
        runner = build_job_runner(RunnerConfig(backend="none"))

        assert isinstance(runner, NullJobRunner)

    @pytest.mark.unit
    def test_empty_backend_falls_back_to_null_runner(self) -> None:
        runner = build_job_runner(RunnerConfig(backend=""))

        assert isinstance(runner, NullJobRunner)

    @pytest.mark.unit
    def test_prefect_backend_returns_prefect_runner(self) -> None:
        runner = build_job_runner(
            RunnerConfig(backend="prefect", job_routing_map={"transcode": "flow/Deployment"})
        )

        assert isinstance(runner, PrefectJobRunner)

    @pytest.mark.unit
    def test_backend_selection_is_case_insensitive(self) -> None:
        runner = build_job_runner(RunnerConfig(backend="PreFect"))

        assert isinstance(runner, PrefectJobRunner)

    @pytest.mark.unit
    def test_webhook_backend_returns_composite_with_webhook_dispatcher(self) -> None:
        runner = build_job_runner(
            RunnerConfig(backend="webhook", webhook_url="https://example.com/hook")
        )

        assert isinstance(runner, CompositeJobRunner)
        assert isinstance(runner._dispatcher, WebhookDispatcher)

    @pytest.mark.unit
    def test_webhook_backend_without_url_raises(self) -> None:
        with pytest.raises(ValueError, match="requires runner_webhook_url"):
            build_job_runner(RunnerConfig(backend="webhook"))

    @pytest.mark.unit
    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown runner_backend"):
            build_job_runner(RunnerConfig(backend="celery"))

    @pytest.mark.unit
    def test_package_export_matches_module(self) -> None:
        # The re-export in app.runners is the same callable as the module.
        assert build_job_runner is build_job_runner_direct
