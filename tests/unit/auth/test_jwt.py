"""Unit tests for the auth-disabled dev-mode bypass in app/auth/jwt.py."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth import jwt as jwt_module
from app.config import AuthConfig


@pytest.mark.unit
def test_get_current_user_returns_dev_principal_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(jwt_module, "get_auth_config", lambda: AuthConfig(disabled=True))

    principal = jwt_module.get_current_user(creds=None)

    assert principal is jwt_module._DEV_PRINCIPAL


@pytest.mark.unit
def test_get_current_user_still_requires_bearer_token_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(jwt_module, "get_auth_config", lambda: AuthConfig(disabled=False))

    with pytest.raises(HTTPException) as exc_info:
        jwt_module.get_current_user(creds=None)

    assert exc_info.value.status_code == 401
