# tests/unit/api/conftest.py
"""Shared fixtures for API router unit tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.auth.jwt import Principal, get_current_user
from app.config import AppConfig, get_media_config
from app.dependencies import (
    get_inbox_service,
    get_job_service,
    get_media_service,
    get_run_summary_service,
    get_scanner_run_summary_service,
    get_runner_state_service,
    get_stream_service,
    get_tag_service,
    get_title_content_service,
    get_title_reference_service,
    get_title_service,
    get_transform_request_service,
    get_id_scheme_service,
)


@pytest.fixture()
def api_app(test_settings: AppConfig) -> Generator:
    """Create FastAPI app with auth bypassed for testing."""
    app = create_app(test_settings)

    app.dependency_overrides[get_media_config] = lambda: test_settings.media

    # Bypass JWT auth for tests
    def _fake_user() -> Principal:
        return Principal(
            sub="test-user",
            email="test@example.com",
            preferred_username="tester",
            azp="test-client",
            roles=["user"],
            token="fake",
            token_payload={"sub": "test-user"},
        )

    app.dependency_overrides[get_current_user] = _fake_user

    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client(api_app) -> Generator[TestClient, None, None]:
    """Provide TestClient for making HTTP requests to the app."""
    with TestClient(api_app) as c:
        yield c


# Wire service mocks from sub-conftest into dependency injection
# The actual mocks are created in subdirectory conftest files


@pytest.fixture()
def media_service_mock(api_app):
    """Wire up MediaService mock to dependency injection - gets mock from assets/conftest."""
    from unittest.mock import create_autospec

    from app.services import MediaService

    mock = create_autospec(MediaService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_media_service] = lambda: mock
    return mock


@pytest.fixture()
def stream_service_mock(api_app):
    """Wire up StreamService mock to dependency injection."""
    from unittest.mock import create_autospec

    from app.services import StreamService

    mock = create_autospec(StreamService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_stream_service] = lambda: mock
    return mock


@pytest.fixture()
def transform_request_service_mock(api_app):
    """Wire up TransformRequestService mock to dependency injection."""
    from unittest.mock import create_autospec

    from app.services import TransformRequestService

    mock = create_autospec(TransformRequestService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_transform_request_service] = lambda: mock
    return mock


@pytest.fixture()
def tag_service_mock(api_app):
    """Wire up TagService mock to dependency injection."""
    from unittest.mock import create_autospec

    from app.services import TagService

    mock = create_autospec(TagService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_tag_service] = lambda: mock
    return mock


@pytest.fixture()
def runner_state_service_mock(api_app):
    """Provide RunnerStateService mock."""
    from unittest.mock import create_autospec

    from app.services import RunnerStateService

    mock = create_autospec(RunnerStateService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_runner_state_service] = lambda: mock
    return mock


@pytest.fixture()
def title_service_mock(api_app):
    """Provide TitleService mock."""
    from unittest.mock import create_autospec

    from app.services import TitleService

    mock = create_autospec(TitleService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_title_service] = lambda: mock
    return mock


@pytest.fixture()
def title_reference_service_mock(api_app):
    """Provide TitleReferenceService mock."""
    from unittest.mock import create_autospec

    from app.services import TitleReferenceService

    mock = create_autospec(TitleReferenceService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_title_reference_service] = lambda: mock
    return mock


@pytest.fixture()
def title_content_service_mock(api_app):
    """Provide TitleContentService mock."""
    from unittest.mock import create_autospec

    from app.services import TitleContentService

    mock = create_autospec(TitleContentService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_title_content_service] = lambda: mock
    return mock


@pytest.fixture()
def inbox_service_mock(api_app):
    """Provide InboxService mock."""
    from unittest.mock import create_autospec

    from app.services import InboxService

    mock = create_autospec(InboxService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_inbox_service] = lambda: mock
    return mock


@pytest.fixture()
def id_scheme_service_mock(api_app):
    """Provide IdSchemeService mock."""
    from unittest.mock import create_autospec

    from app.services import IdSchemeService

    mock = create_autospec(IdSchemeService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_id_scheme_service] = lambda: mock
    return mock


# Keep existing fixtures for other routers that haven't been migrated yet
# TODO: These should be moved to their own conftest files


@pytest.fixture()
def run_summary_service_mock(api_app):
    """Provide RunSummaryService mock (legacy)."""
    from unittest.mock import create_autospec

    from app.services import RunSummaryService

    mock = create_autospec(RunSummaryService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_run_summary_service] = lambda: mock
    return mock


@pytest.fixture()
def scanner_run_summary_service_mock(api_app):
    """Provide ScannerRunSummaryService mock."""
    from unittest.mock import create_autospec

    from app.services import ScannerRunSummaryService

    mock = create_autospec(ScannerRunSummaryService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_scanner_run_summary_service] = lambda: mock
    return mock


@pytest.fixture()
def job_service_mock(api_app):
    """Provide JobService mock (legacy)."""
    from unittest.mock import create_autospec

    from app.services import JobService

    mock = create_autospec(JobService, instance=True, spec_set=True)
    api_app.dependency_overrides[get_job_service] = lambda: mock
    return mock
