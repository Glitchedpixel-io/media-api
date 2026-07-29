# tests/unit/api/test_run_summaries.py
"""Unit tests for run summaries router endpoints."""

from __future__ import annotations

import pytest
from http import HTTPStatus
from fastapi.testclient import TestClient

from app.schemas import RunSummaryCreatePublic
from tests.factories import RunSummaryReadFactory, get_run_summary_creation_json


class TestCreateRunSummary:
    """Tests for POST /api/run_summaries."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_run_summary_success(self, client: TestClient, run_summary_service_mock) -> None:
        """POST /api/run_summaries returns 201 and created run summary."""
        expected_summary = RunSummaryReadFactory()
        run_summary_service_mock.create_run_summary.return_value = expected_summary

        payload = get_run_summary_creation_json(expected_summary)
        response = client.post("/api/run_summaries", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        response_data = response.json()
        assert response_data["id"] == expected_summary.id
        assert response_data["worker_name"] == expected_summary.worker_name

        # Verify service called with correct schema type
        run_summary_service_mock.create_run_summary.assert_called_once()
        call_arg = run_summary_service_mock.create_run_summary.call_args[0][0]
        assert isinstance(call_arg, RunSummaryCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_run_summary_missing_required_field(self, client: TestClient) -> None:
        """POST /api/run_summaries returns 422 when required field missing."""
        invalid_payload = {"worker_name": "worker1"}  # Missing other required fields

        response = client.post("/api/run_summaries", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_run_summary_invalid_field(self, client: TestClient) -> None:
        """POST /api/run_summaries returns 422 for invalid field."""
        invalid_payload = {
            "worker_name": "worker1",
            "worker_type": "system",
            "transform_type": "prefect.test",
            "started_at": "2024-01-01T00:00:00Z",
            "nonexistent_field": "value",
        }

        response = client.post("/api/run_summaries", json=invalid_payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


class TestTransformTypeValidation:
    """Shape-only validation of the provider-qualified routing key on create."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_with_no_dot_is_422(self, client: TestClient, run_summary_service_mock) -> None:
        payload = {
            "worker_name": "worker1",
            "worker_type": "system",
            "transform_type": "prefect",
            "started_at": "2024-01-01T00:00:00Z",
            "processed_count": 1,
            "success_count": 1,
            "failed_count": 0,
            "running_time": 1,
        }

        response = client.post("/api/run_summaries", json=payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        run_summary_service_mock.create_run_summary.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_with_valid_key_reaches_service_unchanged(
        self, client: TestClient, run_summary_service_mock
    ) -> None:
        expected_summary = RunSummaryReadFactory()
        run_summary_service_mock.create_run_summary.return_value = expected_summary
        payload = get_run_summary_creation_json(expected_summary)

        response = client.post("/api/run_summaries", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        call_arg = run_summary_service_mock.create_run_summary.call_args[0][0]
        assert call_arg.transform_type == expected_summary.transform_type
