# app/db.py
from __future__ import annotations

import logfire
from sqlalchemy import create_engine, event, text as sql_text
from sqlalchemy.engine import Engine, ExceptionContext
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DatabaseConfig


class Base(DeclarativeBase):
    pass


# List of error messages that indicate read-only database state
READ_ONLY_ERROR_PATTERNS = [
    "read-only",
    "readonly",
    "read only",
    "database is locked",
    "cannot write",
    "permission denied",
]


def _attach_error_handler(engine: Engine) -> None:
    """Attach the handle_error listener to the given engine."""

    @event.listens_for(engine, "handle_error")
    def handle_engine_errors(context: ExceptionContext) -> None:
        error = context.original_exception
        error_msg = str(error).lower()
        if any(pattern in error_msg for pattern in READ_ONLY_ERROR_PATTERNS):
            # Import here: app.repositories.__init__ imports all repos → schemas → models → app.db
            from app.repositories.errors import DatabaseLocked  # noqa: PLC0415

            raise DatabaseLocked(error_msg) from error


def _create_engine_from_config(config: DatabaseConfig) -> Engine:
    """Create a new SQLAlchemy Engine from the given config."""
    engine = create_engine(
        config.url,
        pool_size=config.pool_size,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True,
        echo=False,
    )

    if config.logfire_for_sqlalchemy:
        logfire.instrument_sqlalchemy(engine=engine)

    _attach_error_handler(engine)
    return engine


_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine not initialized. Call init_db() first.")
    return _engine


def get_session_factory() -> sessionmaker:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized. Call init_db() first.")
    return _session_factory


def init_db(config: DatabaseConfig) -> Engine:
    global _engine, _session_factory  # noqa: PLW0603
    _engine = _create_engine_from_config(config)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    try:
        # Side-effect import that registers the ORM models against Base.metadata.
        # It cannot move to module scope: app/models/__init__.py does
        # `from app.database import Base`, so hoisting raises ImportError on a
        # partially initialized module.
        import app.models  # noqa: F401, PLC0415
    except Exception:
        pass
    return _engine


def _normalize_tz(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().strip('"').strip("'")
    return v


def assert_database_timezone_utc(db_engine: Engine) -> None:
    with logfire.span("assert_database_timezone_utc") as span:
        dialect = db_engine.dialect.name.lower()
        try:
            with db_engine.connect() as conn:
                if dialect == "postgresql":
                    tz = conn.execute(sql_text("SHOW TIME ZONE")).scalar()
                    tz_norm = _normalize_tz(tz)
                    if not tz_norm or "UTC" not in tz_norm.upper():
                        raise RuntimeError(
                            f"Database timezone must be UTC; current PostgreSQL setting is '{tz_norm}'. "
                            "Set 'timezone = 'UTC'' in postgresql.conf or run ALTER DATABASE ... SET timezone TO 'UTC';"
                        )
                    logfire.info(f"PostgreSQL timezone check passed: {tz_norm}")
                    return
                elif dialect in ("mysql", "mariadb"):
                    tz = conn.execute(sql_text("SELECT @@session.time_zone")).scalar()
                    tz_norm = _normalize_tz(tz)
                    if tz_norm and tz_norm.upper() == "SYSTEM":
                        tz = conn.execute(sql_text("SELECT @@system_time_zone")).scalar()
                        tz_norm = _normalize_tz(tz)
                    if not tz_norm or "UTC" not in tz_norm.upper():
                        raise RuntimeError(
                            f"Database timezone must be UTC; current MySQL/MariaDB setting is '{tz_norm}'. "
                            "Set '--default-time-zone=UTC' or 'SET GLOBAL time_zone='UTC'' as appropriate."
                        )
                    logfire.info(f"MySQL/MariaDB timezone check passed: {tz_norm}")
                    return
                elif dialect == "sqlite":
                    logfire.info(
                        "SQLite detected; skipping database timezone check (assume UTC semantics)."
                    )
                    return
                else:
                    logfire.warning(f"Unknown DB dialect '{dialect}'; skipping timezone check.")
                    return
        except RuntimeError as e:
            span.record_exception(e)
            raise
        except Exception as ex:
            logfire.warning(f"Failed to verify database timezone (dialect={dialect}):")
            span.record_exception(ex)
