"""Unit tests for OIDC/auth-related application configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import AuthConfig
from app.config.settings import _Settings


class TestAuthConfig:
    @pytest.mark.unit
    def test_auth_config_defaults(self) -> None:
        cfg = AuthConfig()

        assert cfg.oidc_audience is None
        assert cfg.oidc_jwks_url is None
        assert cfg.oidc_algorithms == "RS256"
        assert cfg.disabled is False


class TestAuthDisabledProductionGuard:
    @pytest.mark.unit
    def test_auth_disabled_allowed_outside_production(self) -> None:
        s = _Settings(env="development", auth_disabled=True)

        assert s.auth_disabled is True

    @pytest.mark.unit
    def test_auth_disabled_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="AUTH_DISABLED"):
            _Settings(env="production", auth_disabled=True)
