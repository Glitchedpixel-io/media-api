# tests/conftest.py
import os
import random
from collections.abc import Generator
from pathlib import Path

import pytest
from faker import Faker
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from dataclasses import replace

from app.config import AppConfig, ElasticsearchConfig, MediaConfig, get_config
from app.models import (
    Base,
    DEFAULT_ARTWORK_KINDS,
    DEFAULT_TITLE_TYPES,
    ArtworkKindORM,
    TitleTypeORM,
)


def pytest_sessionstart(session):
    app_env = os.getenv("APP_ENV")
    if not app_env or app_env.lower() != "test":
        pytest.exit(
            "❌ APP_ENV must be set before running tests (e.g. APP_ENV=test)",
            returncode=1,
        )


@pytest.fixture(scope="session")
def _test_database_url() -> str:
    """Session-scoped database URL for engine creation."""
    return get_config().database.url


@pytest.fixture
def test_settings(
    tmp_path_factory: pytest.TempPathFactory,
) -> AppConfig:
    """Function-scoped config with fresh temp directories for each test."""
    root = tmp_path_factory.mktemp("root")
    media_root = root / "media"
    inbox_root = root / "inbox"
    accessory_root = root / "accessory-store"
    artwork_root = root / "artwork-store"
    media_root.mkdir(parents=True, exist_ok=True)
    inbox_root.mkdir(parents=True, exist_ok=True)
    return replace(
        get_config(),
        media=MediaConfig(
            media_root=str(media_root),
            inbox_root=str(inbox_root),
            accessory_root=str(accessory_root),
            artwork_root=str(artwork_root),
        ),
        elasticsearch=ElasticsearchConfig(),
    )


@pytest.fixture
def media_root(test_settings: AppConfig) -> Path:
    return Path(test_settings.media.media_root)


@pytest.fixture
def accessory_root(test_settings: AppConfig) -> Path:
    return Path(test_settings.media.accessory_root)


@pytest.fixture(scope="session", autouse=True)
def _seed_everything():
    random.seed(1337)
    Faker.seed(1337)


@pytest.fixture(scope="session")
def faker():
    return Faker()


@pytest.fixture(scope="session")
def _test_engine(
    _test_database_url: str,
) -> Generator[Engine, None, None]:
    """Create test database engine with SQLite in-memory or temp file."""

    # Check for explicit test database URL (useful for PostgreSQL integration)
    if _test_database_url:
        engine = create_engine(_test_database_url, future=True)
    else:
        raise ValueError("No database url provided")

    # Create all tables
    Base.metadata.create_all(engine)
    yield engine

    # Cleanup
    engine.dispose()


@pytest.fixture(scope="session")
def _session_factory(_test_engine: Engine) -> sessionmaker:
    """Create session factory for test database."""
    return sessionmaker(bind=_test_engine, autoflush=False, autocommit=False, future=True)


@pytest.fixture
def db_session(
    _session_factory: sessionmaker, _test_engine: Engine
) -> Generator[Session, None, None]:
    """
    Provide a fully isolated database session per test.

    To ensure test isolation even when the application code commits,
    we reset the schema by dropping and recreating all tables before
    each test. This guarantees a clean database state between tests
    for SQLite (in-memory or temp file) and for other databases used
    via TEST_DATABASE_URL.
    """
    # Reset schema to ensure a clean state per test
    Base.metadata.drop_all(bind=_test_engine)
    Base.metadata.create_all(bind=_test_engine)

    # Empty the connection pool afterwards. Dropping and recreating the schema
    # gives the Postgres ENUM types new OIDs, but a pooled connection keeps the
    # OIDs it has already looked up, so reusing one against the new schema fails
    # with "cache lookup failed for type <oid>". Only reachable once more than
    # one connection is in play per test -- which is the case now that requests
    # through TestClient get their own session.
    _test_engine.dispose()

    session = _session_factory()
    seed_title_types(session)
    seed_artwork_kinds(session)
    try:
        yield session
    finally:
        session.close()


def seed_title_types(session: Session) -> None:
    """Seed the reference title types that titles hold a foreign key onto.

    The schema here is built from the models by ``create_all``, not by running
    the migrations, so the seed the migration performs never happens in tests --
    and ``db_session`` drops and recreates every table before each test, so it
    has to happen again each time. Without this, any test that creates a Title
    fails on the foreign key.

    Committed rather than flushed because requests made through ``TestClient``
    get their own session via ``get_db_session`` and would not otherwise see it.

    Args:
        session: The session to seed through.
    """
    session.add_all(TitleTypeORM(code=code, label=label) for code, label in DEFAULT_TITLE_TYPES)
    session.commit()


def seed_artwork_kinds(session: Session) -> None:
    """Seed the reference artwork kinds that artwork holds a foreign key onto.

    Exists for the same reason as ``seed_title_types``: the schema here comes from
    ``create_all`` rather than from the migrations, so the seed the migration performs
    never happens in tests, and ``db_session`` recreates every table before each one.

    Args:
        session: The session to seed through.
    """
    session.add_all(ArtworkKindORM(**seed._asdict()) for seed in DEFAULT_ARTWORK_KINDS)
    session.commit()


@pytest.fixture
def artwork_kind_ids(db_session: Session) -> dict[str, int]:
    """Map seeded artwork kind codes to their IDs, for tests that need the FK."""
    return {k.code: k.id for k in db_session.query(ArtworkKindORM).all()}


@pytest.fixture
def title_type_ids(db_session: Session) -> dict[str, int]:
    """Map seeded title type codes to their IDs, for tests that need the FK."""
    return {tt.code: tt.id for tt in db_session.query(TitleTypeORM).all()}
