# tests/unit/api/test_health.py
"""GET /api/health must not return exception text.

The endpoint is unauthenticated, and a database driver error can name the host,
database and login (CodeQL py/stack-trace-exposure).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import app_factory

SECRET = "connection to db.internal:5432 failed for user media_admin"


class _BrokenEngine:
    dialect = type("Dialect", (), {"name": "postgresql"})()

    def connect(self) -> Any:
        raise RuntimeError(SECRET)


class _BrokenEs:
    def health_check(self) -> dict[str, Any]:
        raise RuntimeError(SECRET)


@pytest.mark.unit
@pytest.mark.api
def test_failures_are_reported_without_their_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each failing service is reported unhealthy, and the error text stays out."""
    monkeypatch.setattr(app_factory, "get_engine", lambda: _BrokenEngine())
    monkeypatch.setattr(app_factory, "get_es_manager", lambda: _BrokenEs())

    response = client.get("/api/health")

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "degraded"
    assert body["services"]["database"] == {"status": "unhealthy"}
    assert body["services"]["elasticsearch"] == {"status": "unavailable", "healthy": False}
    assert "db.internal" not in response.text
    assert "media_admin" not in response.text
