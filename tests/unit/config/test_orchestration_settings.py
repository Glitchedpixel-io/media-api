"""Unit tests for the orchestration-related application configuration."""

from __future__ import annotations

import pytest

from app.config import OrchestrationConfig, get_config, get_orchestration_config
from app.config.settings import _Settings


class TestOrchestrationConfig:
    @pytest.mark.unit
    def test_get_orchestration_config_returns_orchestration_config(self) -> None:
        cfg = get_orchestration_config()

        assert isinstance(cfg, OrchestrationConfig)
        # Same instance as the one held on the composed AppConfig.
        assert cfg is get_config().orchestration

    @pytest.mark.unit
    def test_orchestration_config_has_sensible_shape(self) -> None:
        cfg = get_orchestration_config()

        assert isinstance(cfg.enabled_providers, tuple)
        assert all(isinstance(name, str) for name in cfg.enabled_providers)
        assert hasattr(cfg.provider_options, "get")

    @pytest.mark.unit
    def test_orchestration_config_defaults(self) -> None:
        cfg = OrchestrationConfig()

        assert cfg.enabled_providers == ()
        assert dict(cfg.provider_options) == {}


class TestOrchestrationProviderOptionsEnvParsing:
    """Regression coverage: a blank env var must not crash config loading.

    ORCHESTRATION_PROVIDER_OPTIONS is a dict field, so pydantic-settings
    normally JSON-decodes the raw env string *before* any field validator
    runs -- an unset/blank value ("") is not valid JSON and would otherwise
    raise a SettingsError at startup, breaking the very .env.example template
    this project ships.
    """

    @pytest.mark.unit
    def test_unset_env_var_yields_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORCHESTRATION_PROVIDER_OPTIONS", raising=False)

        s = _Settings()

        assert s.orchestration_provider_options == {}

    @pytest.mark.unit
    def test_blank_env_var_yields_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORCHESTRATION_PROVIDER_OPTIONS", "")

        s = _Settings()

        assert s.orchestration_provider_options == {}

    @pytest.mark.unit
    def test_json_env_var_is_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "ORCHESTRATION_PROVIDER_OPTIONS",
            '{"webhook": {"url": "https://example.com/hook"}}',
        )

        s = _Settings()

        assert s.orchestration_provider_options == {"webhook": {"url": "https://example.com/hook"}}
