"""Unit tests for the runner-related application configuration."""

from __future__ import annotations

import pytest

from app.config import RunnerConfig, get_config, get_runner_config


class TestRunnerConfig:
    @pytest.mark.unit
    def test_get_runner_config_returns_runner_config(self) -> None:
        cfg = get_runner_config()

        assert isinstance(cfg, RunnerConfig)
        # Same instance as the one held on the composed AppConfig.
        assert cfg is get_config().runner

    @pytest.mark.unit
    def test_runner_config_has_sensible_shape(self) -> None:
        cfg = get_runner_config()

        assert isinstance(cfg.backend, str)
        assert cfg.webhook_url is None or isinstance(cfg.webhook_url, str)
        assert not hasattr(cfg, "job_routing_map")

    @pytest.mark.unit
    def test_runner_config_defaults(self) -> None:
        cfg = RunnerConfig()

        assert cfg.backend == "none"
        assert cfg.webhook_url is None
