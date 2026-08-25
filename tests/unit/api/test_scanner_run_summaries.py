# tests/unit/api/test_scanner_run_summaries.py
"""Unit tests for the scanner run summaries router endpoint.

Covers what media-api#37 changed at the edge: a scanner that is not walking a
filesystem can post a summary without inventing values, and `extras` gives it
somewhere to record the counters this shape has no field for.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.schemas import ScannerRunSummaryCreatePublic
from tests.factories import (
    ScannerRunSummaryReadFactory,
    get_scanner_run_summary_creation_json,
)

#: The smallest payload any scanner can produce — no filesystem counters at all.
_MINIMAL_PAYLOAD = {
    "worker_name": "yt-scanner",
    "worker_type": "scanner",
    "started_at": "2024-01-01T00:00:00Z",
    "running_time": 12,
    "dry_run": False,
    "processed_count": 7,
    "previously_seen_count": 3,
}


class TestCreateScannerRunSummary:
    """Tests for POST /api/scanner_run_summaries."""

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_scanner_run_summary_success(
        self, client: TestClient, scanner_run_summary_service_mock
    ) -> None:
        """A full filesystem payload still returns 201."""
        expected = ScannerRunSummaryReadFactory()
        scanner_run_summary_service_mock.create_scanner_run_summary.return_value = expected

        payload = get_scanner_run_summary_creation_json(expected)
        response = client.post("/api/scanner_run_summaries", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["id"] == expected.id

        call_arg = scanner_run_summary_service_mock.create_scanner_run_summary.call_args[0][0]
        assert isinstance(call_arg, ScannerRunSummaryCreatePublic)

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_without_filesystem_counters_is_accepted(
        self, client: TestClient, scanner_run_summary_service_mock
    ) -> None:
        """A non-filesystem scan posts without the columns it cannot answer."""
        scanner_run_summary_service_mock.create_scanner_run_summary.return_value = (
            ScannerRunSummaryReadFactory()
        )

        response = client.post("/api/scanner_run_summaries", json=_MINIMAL_PAYLOAD)

        assert response.status_code == HTTPStatus.CREATED
        call_arg = scanner_run_summary_service_mock.create_scanner_run_summary.call_args[0][0]
        assert call_arg.scan_path is None
        assert call_arg.folder_count is None
        assert call_arg.unsupported_file_count is None
        # ...and what it did send survives intact.
        assert call_arg.processed_count == 7
        assert call_arg.previously_seen_count == 3

    @pytest.mark.unit
    @pytest.mark.api
    def test_extras_reaches_the_service(
        self, client: TestClient, scanner_run_summary_service_mock
    ) -> None:
        """`extras` carries counters this schema has no field for."""
        scanner_run_summary_service_mock.create_scanner_run_summary.return_value = (
            ScannerRunSummaryReadFactory()
        )
        payload = {**_MINIMAL_PAYLOAD, "extras": {"items_seen": 40, "created": 7}}

        response = client.post("/api/scanner_run_summaries", json=payload)

        assert response.status_code == HTTPStatus.CREATED
        call_arg = scanner_run_summary_service_mock.create_scanner_run_summary.call_args[0][0]
        assert call_arg.extras == {"items_seen": 40, "created": 7}

    @pytest.mark.unit
    @pytest.mark.api
    @pytest.mark.parametrize(
        "missing",
        ["worker_name", "worker_type", "started_at", "running_time", "dry_run"],
    )
    def test_create_missing_universal_field_is_422(
        self, client: TestClient, scanner_run_summary_service_mock, missing: str
    ) -> None:
        """Relaxing the filesystem fields must not relax the rest."""
        payload = {k: v for k, v in _MINIMAL_PAYLOAD.items() if k != missing}

        response = client.post("/api/scanner_run_summaries", json=payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        scanner_run_summary_service_mock.create_scanner_run_summary.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.api
    def test_create_unknown_field_is_still_422(
        self, client: TestClient, scanner_run_summary_service_mock
    ) -> None:
        """`extra: "forbid"` still holds — `extras` is the only free-form route."""
        payload = {**_MINIMAL_PAYLOAD, "nonexistent_field": "value"}

        response = client.post("/api/scanner_run_summaries", json=payload)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        scanner_run_summary_service_mock.create_scanner_run_summary.assert_not_called()
