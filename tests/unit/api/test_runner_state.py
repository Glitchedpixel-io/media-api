# tests/unit/api/test_runner_state.py
"""Unit tests for runner state router endpoints."""

from __future__ import annotations

from unittest.mock import ANY

import pytest
from http import HTTPStatus
from fastapi import HTTPException
from fastapi.testclient import TestClient


class TestGetRunnerState:
    """Tests for GET /api/runner_state/{runner_key}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_runner_state_success(self, client: TestClient, runner_state_service_mock) -> None:
        """GET /api/runner_state/{runner_key} returns the runner state."""
        runner_state_service_mock.get_runner_state.return_value = {
            "runner_key": "scanner",
            "state": {"offset": 42},
            "updated_at": "2024-01-01T00:00:00Z",
        }

        response = client.get("/api/runner_state/scanner")

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["runner_key"] == "scanner"
        assert response_data["state"]["offset"] == 42
        runner_state_service_mock.get_runner_state.assert_called_once_with("scanner")

    @pytest.mark.unit
    @pytest.mark.api
    def test_get_runner_state_not_found(
        self, client: TestClient, runner_state_service_mock
    ) -> None:
        """GET /api/runner_state/{runner_key} returns 404 when not found."""
        runner_state_service_mock.get_runner_state.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Runner State not found"
        )

        response = client.get("/api/runner_state/unknown")

        assert response.status_code == HTTPStatus.NOT_FOUND
        runner_state_service_mock.get_runner_state.assert_called_once_with("unknown")


class TestCreateRunnerState:
    """Tests for POST /api/runner_state."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_runner_state_success(
        self, client: TestClient, runner_state_service_mock
    ) -> None:
        """POST /api/runner_state returns 201 and created runner state."""
        runner_state_service_mock.create_runner_state.return_value = {
            "runner_key": "generated-key",
            "state": {"page": 1},
            "updated_at": "2024-01-01T00:00:00Z",
        }

        response = client.post("/api/runner_state", json={"state": {"page": 1}})

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["runner_key"] == "generated-key"
        assert response_data["state"]["page"] == 1
        runner_state_service_mock.create_runner_state.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_runner_state_invalid_type(
        self, client: TestClient, runner_state_service_mock
    ) -> None:
        """POST /api/runner_state returns 422 for invalid state type."""
        response = client.post("/api/runner_state", json={"state": "not-a-dict"})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        runner_state_service_mock.create_runner_state.assert_not_called()


class TestUpdateRunnerState:
    """Tests for PATCH /api/runner_state/{runner_key}."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_runner_state_success(
        self, client: TestClient, runner_state_service_mock
    ) -> None:
        """PATCH /api/runner_state/{runner_key} updates the runner state."""
        runner_state_service_mock.update_runner_state.return_value = {
            "runner_key": "scanner",
            "state": {"offset": 100},
            "updated_at": "2024-01-01T00:00:00Z",
        }

        response = client.patch(
            "/api/runner_state/scanner",
            json={"state": {"offset": 100}},
        )

        assert response.status_code == HTTPStatus.OK
        response_data = response.json()
        assert response_data["runner_key"] == "scanner"
        assert response_data["state"]["offset"] == 100

        # Verify service called with exclude_none=True for PATCH
        runner_state_service_mock.update_runner_state.assert_called_once_with("scanner", ANY, True)

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_runner_state_invalid_type(
        self, client: TestClient, runner_state_service_mock
    ) -> None:
        """PATCH /api/runner_state/{runner_key} returns 422 for invalid state type."""
        response = client.patch(
            "/api/runner_state/scanner",
            json={"state": "invalid"},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        runner_state_service_mock.update_runner_state.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.api
    def test_update_runner_state_not_found(
        self, client: TestClient, runner_state_service_mock
    ) -> None:
        """PATCH /api/runner_state/{runner_key} returns 404 when not found."""
        runner_state_service_mock.update_runner_state.side_effect = HTTPException(
            status_code=HTTPStatus.NOT_FOUND, detail="Runner State not found"
        )

        response = client.patch(
            "/api/runner_state/nonexistent",
            json={"state": {"offset": 100}},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND
