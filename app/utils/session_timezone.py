"""Pin every database session to UTC.

Core's production servers default a new session to ``Europe/London``, and psycopg hands
back each ``timestamptz`` zoned to the session. In the hour repeated when the clocks go
back (00:00-02:00 UTC on the last Sunday of October), a London datetime is never ``==``
to the same instant in any other zone (PEP 495), so equality, ``in`` and dict-key
matching against a UTC value silently fail. The test databases default to UTC and never
showed it. The fix and its write-up: sentinel-core#525 (D-239).

Every connect path takes its libpq ``options`` from here, so a session reads and writes
in UTC whatever the server's default. This module imports nothing heavy at load time,
so the tools that keep their database driver optional can use it too.
"""

from __future__ import annotations

from typing import Any

SESSION_TIMEZONE = "-c TimeZone=UTC"
"""The libpq ``options`` that make a session read and write ``timestamptz`` in UTC."""


def session_options(own: str | None = None) -> str:
    """Work out the libpq ``options`` for a connection: any already set, then the pin.

    Args:
        own (str | None): ``options`` the connection already carries, kept in front.

    Returns:
        str: The ``options`` to connect with.
    """
    return f"{own} {SESSION_TIMEZONE}" if own else SESSION_TIMEZONE


def engine_connect_args(url: str) -> dict[str, Any]:
    """The ``connect_args`` that pin a SQLAlchemy engine's sessions to UTC.

    ``connect_args`` replaces an ``options`` given in the URL's query rather than adding
    to it, so any the URL carries (a ``search_path``, a ``statement_timeout``) are kept
    in front of the pin.

    Args:
        url (str): The engine's SQLAlchemy URL.

    Returns:
        dict[str, Any]: The engine's ``connect_args``.
    """
    from sqlalchemy.engine import make_url  # noqa: PLC0415 -- keep import-time light.

    own = make_url(url).query.get("options")
    if isinstance(own, tuple):
        own = " ".join(own)
    return {"options": session_options(own)}


def pinned_conninfo(dsn: str) -> str:
    """A libpq connection string with its session pinned to UTC.

    For a bare ``psycopg.connect``: an ``options=`` keyword would replace the one in the
    DSN, so any the DSN carries are kept in front of the pin.

    Args:
        dsn (str): A libpq connection string or ``postgresql://`` URL (no SQLAlchemy
            ``+driver`` suffix).

    Returns:
        str: The same connection, with the pin in its ``options``.

    Raises:
        psycopg.ProgrammingError: If ``dsn`` is not a valid connection string.
    """
    from psycopg.conninfo import (  # noqa: PLC0415 -- the driver stays optional here.
        conninfo_to_dict,
        make_conninfo,
    )

    own = conninfo_to_dict(dsn).get("options")
    return make_conninfo(dsn, options=session_options(str(own) if own else None))
