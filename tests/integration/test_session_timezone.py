"""Sessions in UTC, on a database that defaults new sessions to London.

Core's production servers default a session to `Europe/London`; the test databases
default to UTC, where none of this shows. These tests make a throwaway database whose
sessions default to London, as production's do, and go through the service's real
connect paths -- the app engine and the Alembic engine (sentinel-core#525, D-239). The
shared test database is never altered.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import event, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import Pool

from app.config import DatabaseConfig, get_config
from app.database import Base, _create_engine_from_config, assert_database_timezone_utc
from app.models import TagORM
from app.schemas import TagRead

pytestmark = pytest.mark.integration

# In the hour the clocks repeat on 2026-10-25: 01:30 London, twice.
FOLD = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def _libpq(url: URL) -> str:
    """The URL as libpq takes it, without SQLAlchemy's `+driver`."""
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture
def london() -> Iterator[str]:
    """A throwaway database whose new sessions default to Europe/London.

    Yields:
        str: Its SQLAlchemy URL.
    """
    shared = make_url(get_config().database.url)
    name = f"tz_london_{secrets.token_hex(4)}"
    target = sql.Identifier(name)
    with psycopg.connect(_libpq(shared), autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(target))
    try:
        with psycopg.connect(_libpq(shared), autocommit=True) as conn:
            conn.execute(sql.SQL("alter database {} set timezone = 'Europe/London'").format(target))
        yield shared.set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(_libpq(shared), autocommit=True) as conn:
            conn.execute(sql.SQL("drop database if exists {} with (force)").format(target))


def test_the_throwaway_database_defaults_to_london(london: str) -> None:
    # Without this, the tests below would prove nothing.
    with psycopg.connect(_libpq(make_url(london))) as conn:
        assert conn.execute("show timezone").fetchone() == ("Europe/London",)


def test_the_app_engine_is_in_utc_and_a_fold_hour_time_comes_back_equal(
    london: str,
) -> None:
    engine = _create_engine_from_config(DatabaseConfig(url=london))
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(TagORM(name="fold", created_at=FOLD, updated_at=FOLD))
            session.commit()
        with Session(engine) as session:
            tag = session.scalars(select(TagORM)).one()
            assert tag.created_at == FOLD
            assert TagRead.model_validate(tag).model_dump(mode="json")["created_at"] == (
                "2026-10-25T00:30:00Z"
            )
        with engine.connect() as conn:
            assert conn.execute(text("show timezone")).scalar() == "UTC"
        assert_database_timezone_utc(engine)
    finally:
        engine.dispose()


def test_migrations_run_in_utc_and_keep_the_urls_options(
    london: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with psycopg.connect(_libpq(make_url(london)), autocommit=True) as conn:
        conn.execute("create schema elsewhere")
    url = (
        make_url(london)
        .update_query_dict({"options": "-csearch_path=elsewhere"})
        .render_as_string(hide_password=False)
    )
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    monkeypatch.delenv("ALEMBIC_OWNER_ROLE", raising=False)
    # No ini file, so env.py leaves the test run's logging alone.
    config = Config()
    config.set_main_option("script_location", str(ROOT / "alembic"))

    seen: list[Any] = []

    def on_connect(dbapi_conn: Any, _record: Any) -> None:
        seen.append(dbapi_conn.execute("show timezone").fetchone())

    event.listen(Pool, "connect", on_connect)
    try:
        command.upgrade(config, "head")
    finally:
        event.remove(Pool, "connect", on_connect)

    assert seen == [("UTC",)]
    with psycopg.connect(_libpq(make_url(london))) as conn:
        found = conn.execute(
            "select table_schema from information_schema.tables where table_name = 'tags'"
        ).fetchall()
    assert found == [("elsewhere",)]
