"""
FastAPI Integration Test Configuration

Sets up test infrastructure for full-stack integration tests that cover:
- API requests through FastAPI routers
- Service layer business logic
- Repository data access layer
- Database interactions against the Postgres instance at TEST_DATABASE_URL

Uses dependency injection overrides to provide test database sessions
while maintaining the same code paths as production.
"""

from collections.abc import Generator

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.app_factory import create_app
from app.auth.jwt import Principal, get_current_user
from app.config import AppConfig, get_media_config
from app.dependencies import get_db_session, get_inbox_repository
from app.orchestration.loader import build_provider_registry
from app.orchestration.registry import get_provider_registry
from app.repositories import (
    FileInboxRepository,
    SQLAlchemyRunSummaryRepository,
    SQLAlchemyStreamRepository,
    SQLAlchemyTagRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleReferenceRepository,
    SQLAlchemyTitleRepository,
    SQLAlchemyTransformRequestRepository,
)
from app.repositories.media_repository import SQLAlchemyMediaRepository
from app.repositories.protocols import (
    InboxRepository,
    MediaRepository,
    RunSummaryRepository,
    StreamRepository,
    TagRepository,
    TitleContentRepository,
    TitleReferenceRepository,
    TitleRepository,
    TransformRequestRepository,
)


@pytest.fixture
def app(
    db_session: Session,
    _session_factory: sessionmaker,
    inbox_repository: InboxRepository,
    test_settings: AppConfig,
) -> Generator[FastAPI, None, None]:
    """Build the app with test overrides in place.

    ``db_session`` is depended on even though requests no longer use it: that
    fixture is what resets the schema and seeds the title types for this test.
    """
    app = create_app(test_settings)

    app.dependency_overrides[get_media_config] = lambda: test_settings.media
    app.dependency_overrides[get_provider_registry] = lambda: build_provider_registry(
        test_settings.orchestration
    )

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

    # One session per request, mirroring get_db_session in app/dependencies.py.
    # A SQLAlchemy Session is not thread-safe and TestClient runs this repo's sync
    # endpoints in a threadpool, so sharing the test body's session across requests
    # would put one Session in several threads at once.
    #
    # This means a request sees the test body's data only once it is committed.
    # The repositories commit (see SQLAlchemyBaseRepository._safe_commit), as does
    # seed_title_types, so setup done through those is visible; raw db_session.add()
    # without a commit is not.
    def override_db_session() -> Generator[Session, None, None]:
        db = _session_factory()
        try:
            yield db
        finally:
            db.close()

    def override_inbox_repository() -> InboxRepository:
        return inbox_repository

    app.dependency_overrides[get_db_session] = override_db_session

    app.dependency_overrides[get_inbox_repository] = override_inbox_repository

    yield app

    # Clean up dependency overrides
    app.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create test client for making HTTP requests to the API."""
    return TestClient(app)


# Repository fixtures for direct testing of repository layer
@pytest.fixture
def media_repository(db_session: Session) -> MediaRepository:
    """Media repository for testing data access layer."""
    return SQLAlchemyMediaRepository(db_session)


@pytest.fixture
def stream_repository(db_session: Session) -> StreamRepository:
    """Stream repository for testing data access layer."""
    return SQLAlchemyStreamRepository(db_session)


@pytest.fixture
def transform_request_repository(db_session: Session) -> TransformRequestRepository:
    """Transform request repository for testing data access layer."""
    return SQLAlchemyTransformRequestRepository(db_session)


@pytest.fixture
def run_summary_repository(db_session: Session) -> RunSummaryRepository:
    """Run summary repository for testing data access layer."""
    return SQLAlchemyRunSummaryRepository(db_session)


@pytest.fixture
def title_repository(db_session: Session) -> TitleRepository:
    """Title repository for testing data access layer."""
    return SQLAlchemyTitleRepository(db_session)


@pytest.fixture
def title_reference_repository(db_session: Session) -> TitleReferenceRepository:
    """Title reference repository for testing data access layer."""
    return SQLAlchemyTitleReferenceRepository(db_session)


@pytest.fixture
def title_content_repository(db_session: Session) -> TitleContentRepository:
    """Title content repository for testing data access layer."""
    return SQLAlchemyTitleContentRepository(db_session)


@pytest.fixture
def tag_repository(db_session: Session) -> TagRepository:
    """Tag repository for testing data access layer."""
    return SQLAlchemyTagRepository(db_session)


@pytest.fixture
def inbox_repository(test_settings: AppConfig) -> InboxRepository:
    return FileInboxRepository(test_settings.media)
