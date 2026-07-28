# tests/integration/api/test_runner_state_api.py
from __future__ import annotations

from http import HTTPStatus
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import RunnerStateORM


@pytest.mark.integration
@pytest.mark.api
class TestRunnerStateAPI:
    """Integration tests for /api/runner_state endpoints."""

    def test_create_and_get_runner_state(self, client: TestClient, db_session: Session) -> None:
        # Create a new runner state via API
        create_resp = client.post(
            "/api/runner_state",
            json={"state": {"offset": 5, "page": 1}},
        )
        assert create_resp.status_code == HTTPStatus.CREATED
        created = create_resp.json()
        assert created["runner_key"] and isinstance(created["runner_key"], str)
        assert created["state"] == {"offset": 5, "page": 1}
        assert created["updated_at"] is not None

        # Verify it persisted by querying the DB directly
        db_session.commit()
        db_obj = db_session.get(RunnerStateORM, created["runner_key"])
        assert db_obj is not None
        assert db_obj.runner_key == created["runner_key"]
        assert db_obj.state == {"offset": 5, "page": 1}

        # Now GET via API should return the same
        get_resp = client.get(f"/api/runner_state/{created['runner_key']}")
        assert get_resp.status_code == HTTPStatus.OK
        got = get_resp.json()
        assert got["runner_key"] == created["runner_key"]
        assert got["state"] == {"offset": 5, "page": 1}
        assert got["updated_at"] is not None

    def test_patch_runner_state_updates(self, client: TestClient, db_session: Session) -> None:
        # Seed one by creating first
        create_resp = client.post(
            "/api/runner_state",
            json={"state": {"offset": 10}},
        )
        assert create_resp.status_code == HTTPStatus.CREATED
        created = create_resp.json()
        key = created["runner_key"]

        # Patch to update state
        patch_resp = client.patch(
            f"/api/runner_state/{key}",
            json={"state": {"offset": 99, "extra": True}},
        )
        assert patch_resp.status_code == HTTPStatus.OK
        patched = patch_resp.json()
        assert patched["runner_key"] == key
        assert patched["state"] == {"offset": 99, "extra": True}

        # Verify via GET
        get_resp = client.get(f"/api/runner_state/{key}")
        assert get_resp.status_code == HTTPStatus.OK
        got = get_resp.json()
        assert got["state"] == {"offset": 99, "extra": True}

        # And via DB
        db_session.commit()
        db_obj = db_session.get(RunnerStateORM, key)
        assert db_obj is not None
        assert db_obj.state == {"offset": 99, "extra": True}

    def test_get_missing_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/runner_state/does-not-exist")
        assert resp.status_code == HTTPStatus.NOT_FOUND
