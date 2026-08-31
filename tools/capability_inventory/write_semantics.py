"""Phase 6 -- write semantics, error taxonomy and the constraint map.

The read side of this harness answers what an endpoint costs. This phase answers
what a *form* has to handle: what it must send, what it may omit, what comes back
when it is wrong, and what state the system is left in when a write half-succeeds.

Phase 6 is the only phase that mutates anything, and it is the only phase that can
destroy data if it is pointed at the wrong place. Everything in the first half of
this module exists to make that impossible rather than unlikely:

1. ``--allow-writes`` must be passed explicitly.
2. Both write variables must be set.
3. Neither may resolve to the same database as its read-side counterpart. String
   inequality is not identity -- ``localhost`` and ``127.0.0.1``, a different user
   and a different ``sslmode`` all spell one database three ways -- so the
   comparison is made on a normalised ``(host, port, dbname)`` tuple *and* on the
   cluster's own ``system_identifier``, which no DSN spelling can fool.
4. The instance at ``CAPINV_WRITE_BASE_URL`` must be demonstrably backed by
   ``CAPINV_WRITE_DATABASE_URL``. Nothing in steps 1-3 establishes that: a write
   base URL pointing at production and a write DSN pointing at scratch passes
   every one of them and then writes to production over HTTP. :func:`bind_check`
   settles it positively, by writing a sentinel row through the database and
   reading it back through the API.

Gate 4 is the one that matters. The others compare configuration against
configuration; only the sentinel compares the target against itself.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import Unknown

BASE_URL_ENV = "CAPINV_WRITE_BASE_URL"
DATABASE_URL_ENV = "CAPINV_WRITE_DATABASE_URL"
MEDIA_ROOT_ENV = "CAPINV_WRITE_MEDIA_ROOT"
TOKEN_ENV = "CAPINV_WRITE_TOKEN"

_READ_BASE_URL_ENV = "CAPINV_BASE_URL"
_READ_DATABASE_URL_ENV = "CAPINV_DATABASE_URL"

# Known production DSNs, as the digest `data_shape.fingerprint` already records in
# the committed report: `sha256(dsn)[:12]`.
#
# This catches the exact DSN string and nothing else -- it is a digest of the URL,
# not of the cluster, so a respelling of the same database sails past it. That is
# not a flaw to fix here but the reason the cluster-identity gate below exists:
# this constant stops a known-bad paste, `cluster_fingerprint` stops everything
# else. Recording the wrong kind of value here would be worse than recording
# none, because a gate that cannot fire still reads as protection.
_FORBIDDEN_DSN_DIGESTS = frozenset({"5ee609328cdc"})


def dsn_digest(dsn: str) -> str:
    """The digest `data_shape.fingerprint` publishes, for comparison against it."""
    return hashlib.sha256(dsn.encode("utf-8")).hexdigest()[:12]


class WriteTargetError(RuntimeError):
    """Raised when the write target is unsafe, unproven or unconfigured.

    Every message names the variable or the check that failed and what would
    satisfy it. A refusal that does not say what to do next gets worked around.
    """


@dataclass(frozen=True)
class WriteTarget:
    """A validated, disposable write target.

    Attributes:
        base_url: Instance the probes issue requests against.
        database_url: The database that instance is backed by, used for
            verification and for cleanup the API cannot express.
        media_root: A scratch media root, or None. Filesystem-touching probes
            are skipped and recorded UNKNOWN when it is None -- never run
            against a real one.
        fingerprint: ``system_identifier/database`` of the write cluster.
        token: Bearer token, when the instance enforces auth.
    """

    base_url: str
    database_url: str
    media_root: str | None
    fingerprint: str
    token: str | None = None


def normalise_dsn(dsn: str) -> tuple[str, str, str]:
    """Reduce a DSN to the identity that matters: host, port and database.

    Username, password, driver suffix and query parameters are all discarded.
    Two DSNs differing only in those spell the same database, and comparing them
    as strings would report them as different -- which is exactly the mistake
    that lets a "distinct from the read side" check pass against production.

    Args:
        dsn: A PostgreSQL connection URL, with or without a driver suffix.

    Returns:
        ``(host, port, database)``, lower-cased, with loopback spellings folded
        together and the default port made explicit.
    """
    scheme, _, rest = dsn.partition("://")
    cleaned = f"{scheme.split('+', 1)[0]}://{rest}"
    parts = urlsplit(cleaned)
    host = (parts.hostname or "").lower()
    # localhost and 127.0.0.1 are the same machine spelled two ways.
    if host in {"127.0.0.1", "::1", "localhost"}:
        host = "localhost"
    port = str(parts.port or 5432)
    database = parts.path.lstrip("/").split("?", 1)[0]
    return host, port, database


def psycopg_dsn(dsn: str) -> str:
    """Strip a SQLAlchemy driver suffix so psycopg can consume the URL."""
    scheme, sep, rest = dsn.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}"


def cluster_fingerprint(dsn: str) -> str:
    """Fingerprint a cluster by its own identity rather than by its URL.

    ``pg_control_system().system_identifier`` is generated at ``initdb`` and is
    unique per cluster; ``current_database()`` names the database within it.
    Together they identify a target no matter how the DSN reaching it is spelled.

    Args:
        dsn: Connection URL for the database to fingerprint.

    Returns:
        ``<system_identifier>/<database>``, the identifier truncated to a stable
        short form.

    Raises:
        WriteTargetError: If the database cannot be reached.
    """
    import psycopg  # noqa: PLC0415 -- optional at import time; only Phase 6 needs it.

    try:
        with psycopg.connect(psycopg_dsn(dsn), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT system_identifier::text, current_database() FROM pg_control_system()"
                )
                row = cur.fetchone()
    except Exception as exc:  # pragma: no cover - environment dependent
        raise WriteTargetError(f"cannot reach the database to fingerprint it: {exc}") from exc
    if row is None:  # pragma: no cover - defensive
        raise WriteTargetError("the database returned no fingerprint")
    identifier, database = row
    return f"{identifier[-12:]}/{database}"


def resolve_target(*, allow_writes: bool) -> WriteTarget:
    """Validate configuration and return a write target, or refuse.

    Args:
        allow_writes: Whether ``--allow-writes`` was passed.

    Returns:
        A validated :class:`WriteTarget`.

    Raises:
        WriteTargetError: If any gate fails. The message names the gate that did.
    """
    if not allow_writes:
        raise WriteTargetError(
            "Phase 6 mutates data and needs --allow-writes in addition to "
            f"{BASE_URL_ENV} and {DATABASE_URL_ENV}. Without it nothing is written and "
            "every write contract is reported UNKNOWN"
        )

    base_url = (os.environ.get(BASE_URL_ENV) or "").strip()
    database_url = (os.environ.get(DATABASE_URL_ENV) or "").strip()
    missing = [
        name
        for name, value in ((BASE_URL_ENV, base_url), (DATABASE_URL_ENV, database_url))
        if not value
    ]
    if missing:
        raise WriteTargetError(
            f"{' and '.join(missing)} must be set for Phase 6. Point them at a "
            "disposable instance and its own database -- never at the instance or "
            "database the read phases use"
        )

    read_base = (os.environ.get(_READ_BASE_URL_ENV) or "").strip()
    read_db = (os.environ.get(_READ_DATABASE_URL_ENV) or "").strip()

    if read_base and base_url.rstrip("/") == read_base.rstrip("/"):
        raise WriteTargetError(
            f"{BASE_URL_ENV} is the same instance as {_READ_BASE_URL_ENV}. The read "
            "phases run against a production-backed instance; Phase 6 must not"
        )
    if read_db and normalise_dsn(database_url) == normalise_dsn(read_db):
        host, port, name = normalise_dsn(database_url)
        raise WriteTargetError(
            f"{DATABASE_URL_ENV} resolves to the same database as "
            f"{_READ_DATABASE_URL_ENV} ({host}:{port}/{name}), however the two URLs "
            "are spelled. Phase 6 must have a database of its own"
        )

    if dsn_digest(database_url) in _FORBIDDEN_DSN_DIGESTS:
        raise WriteTargetError(
            f"{DATABASE_URL_ENV} digests as {dsn_digest(database_url)}, which is a "
            "known production DSN. Refusing to write to it"
        )
    fingerprint = cluster_fingerprint(database_url)
    if read_db:
        try:
            same = cluster_fingerprint(read_db) == fingerprint
        except WriteTargetError:
            same = False  # The read database being offline is not a Phase 6 failure.
        if same:
            raise WriteTargetError(
                f"{DATABASE_URL_ENV} and {_READ_DATABASE_URL_ENV} fingerprint "
                f"identically as {fingerprint}: they are one database reached by two "
                "URLs. Phase 6 must have a database of its own"
            )

    media_root = (os.environ.get(MEDIA_ROOT_ENV) or "").strip() or None
    token = (os.environ.get(TOKEN_ENV) or os.environ.get("CAPINV_TOKEN") or "").strip() or None
    return WriteTarget(
        base_url=base_url.rstrip("/"),
        database_url=database_url,
        media_root=media_root,
        fingerprint=fingerprint,
        token=token,
    )


def bind_check(target: WriteTarget) -> None:
    """Prove the instance and the database are one system, before any probe runs.

    Writes a uniquely-named sentinel tag directly through ``database_url``, then
    reads it back through ``base_url``. If the API cannot see it, the two point at
    different systems and every subsequent probe would mutate something nobody
    asked it to. The sentinel is removed either way.

    This is the only check that establishes the binding positively. Comparing the
    write variables against the read variables compares configuration against
    configuration, and passes cleanly for a write base URL aimed at production.

    Args:
        target: The target to verify.

    Raises:
        WriteTargetError: If the sentinel cannot be written, or is not visible
            through the API.
    """
    import httpx  # noqa: PLC0415 -- Phase 6 only.
    import psycopg  # noqa: PLC0415 -- Phase 6 only.

    # `tags.name` is varchar(50), so the sentinel is built to fit inside it. A full
    # uuid4 in its dashed form overflows by two characters, and the insert then
    # fails with a type error that reads as a binding failure -- aborting the run
    # while pointing the operator at entirely the wrong problem.
    sentinel = f"capinv-sentinel-{uuid.uuid4().hex[:16]}"
    dsn = psycopg_dsn(target.database_url)
    tag_id: int | None = None
    try:
        try:
            with psycopg.connect(dsn, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    # `color` is NOT NULL with no server default, so it has to be
                    # supplied. A sentinel that fails on the schema would abort the
                    # run with a message about binding, which is not what went wrong.
                    cur.execute(
                        "INSERT INTO tags (name, color) VALUES (%s, %s) RETURNING id",
                        (sentinel, "#000000"),
                    )
                    row = cur.fetchone()
                    tag_id = int(row[0]) if row else None
                conn.commit()
        except Exception as exc:
            raise WriteTargetError(
                f"could not write a sentinel row to {DATABASE_URL_ENV}: {exc}"
            ) from exc
        if tag_id is None:  # pragma: no cover - defensive
            raise WriteTargetError("the sentinel row could not be created")

        headers = {"Authorization": f"Bearer {target.token}"} if target.token else {}
        try:
            response = httpx.get(
                f"{target.base_url}/api/tags/{tag_id}", headers=headers, timeout=30.0
            )
        except Exception as exc:
            raise WriteTargetError(
                f"{BASE_URL_ENV} ({target.base_url}) could not be reached to verify it is "
                f"backed by {DATABASE_URL_ENV}: {exc}"
            ) from exc

        seen = None
        if response.status_code == 200:
            try:
                seen = response.json().get("name")
            except ValueError:  # pragma: no cover - defensive
                seen = None
        if seen != sentinel:
            raise WriteTargetError(
                f"the instance at {target.base_url} cannot see a row written to "
                f"{target.fingerprint} (GET /api/tags/{tag_id} returned "
                f"{response.status_code}). {BASE_URL_ENV} is NOT backed by "
                f"{DATABASE_URL_ENV}, so every write probe would mutate a different "
                "system than the one this run verified. Refusing to continue"
            )
    finally:
        if tag_id is not None:
            try:
                with psycopg.connect(dsn, connect_timeout=10) as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM tags WHERE id = %s", (tag_id,))
                    conn.commit()
            except Exception:  # pragma: no cover - surfaced by the cleanup ledger
                pass


def skipped_unknown(reason: str) -> Unknown:
    """The run-wide gap recorded when Phase 6 does not run.

    Args:
        reason: Why it did not run, in the terms the operator will recognise.

    Returns:
        An :class:`Unknown` naming the concrete thing that would settle it.
    """
    return Unknown(
        scope="Phase 6",
        question=(
            "what a write endpoint requires, what it returns when it fails, and what "
            "state it leaves behind when it half-succeeds"
        ),
        resolution=(
            f"{reason}; re-run with --allow-writes and with {BASE_URL_ENV} and "
            f"{DATABASE_URL_ENV} pointing at a disposable instance and its own database "
            "(scripts/rehearse_migration.sh --for-write-probes builds one)"
        ),
    )
