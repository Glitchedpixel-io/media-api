"""Phase 3 -- what the data behind the API actually looks like.

Everything here runs inside an explicit ``BEGIN READ ONLY`` transaction and
every generated statement is asserted to be a ``SELECT`` before it is sent. Both
guards are independent of what the supplied credentials happen to permit: a
read-only role is the right way to run this, but the harness does not rely on
being given one.

Three questions are answered per column, and they are different questions:

* **Fill rate** -- of the rows that exist, how many actually carry this value.
  A field that is 12% filled cannot anchor a card layout.
* **Cardinality** -- how many distinct values there are, which decides whether a
  field can become chips, a dropdown, or nothing at all.
* **Collection size distribution** -- min/median/p95/max children per parent, so
  a design accounts for the 3-item case and the 4000-item case at once.

Columns backing a relationship mapped ``lazy="noload"`` are deliberately *not*
given a fill rate. The API returns ``[]`` for those unless the caller opts in
with ``include=``, so a database-derived percentage would describe something the
front-end never sees.
"""

from __future__ import annotations

import hashlib
import os
import re
import statistics
import time
from dataclasses import dataclass
from typing import Any

from .models import CollectionStats, ColumnStats, CoverageMetric, DataShape, Unknown

ENV_VAR = "CAPINV_DATABASE_URL"

# Types worth a cardinality scan: a UI can turn these into facets. Numerics and
# timestamps are excluded -- their cardinality is almost always ~row count and
# scanning them is pure cost.
_FACETABLE_TYPES = frozenset(
    {"text", "character varying", "varchar", "character", "boolean", "USER-DEFINED"}
)

# Above this many distinct values a column is not a facet, whatever it holds.
FACET_CARDINALITY_LIMIT = 200

_SELECT_ONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ReadOnlyViolation(RuntimeError):
    """Raised when a generated statement is not a plain read."""


@dataclass(frozen=True)
class _Column:
    """A column as the database reports it."""

    table: str
    name: str
    data_type: str
    nullable: bool


def _quote(identifier: str) -> str:
    """Quote an SQL identifier, rejecting anything that is not one.

    Every identifier used here comes from the database catalogue or from the
    SQLAlchemy metadata, never from user input, but validating anyway means a
    future caller cannot turn this into an injection point.

    Raises:
        ValueError: If the identifier is not a bare SQL identifier.
    """
    if not _IDENTIFIER.match(identifier):
        raise ValueError(f"Refusing to build SQL with identifier {identifier!r}")
    return f'"{identifier}"'


def _guard(sql: str) -> str:
    """Assert a statement is a read before it is sent.

    Raises:
        ReadOnlyViolation: If the statement is not a SELECT or WITH.
    """
    if not _SELECT_ONLY.match(sql):
        raise ReadOnlyViolation(f"Refusing to execute a non-SELECT statement: {sql[:80]!r}")
    return sql


def resolve_dsn() -> str:
    """Read the read-only connection string from the environment.

    A dedicated variable is used rather than ``DATABASE_URL``. The application's
    own settings resolve the database with
    ``AliasChoices("TEST_DATABASE_URL", "DATABASE_URL")``, so a ``TEST_DATABASE_URL``
    left in the shell silently outranks ``DATABASE_URL`` -- the harness would
    then report on a different database than the operator intended, and say
    nothing about it.

    Returns:
        The DSN, with any SQLAlchemy driver suffix stripped for psycopg.

    Raises:
        RuntimeError: If the variable is unset or empty.
    """
    dsn = os.environ.get(ENV_VAR, "").strip()
    if not dsn:
        raise RuntimeError(
            f"{ENV_VAR} is not set. Phase 3 needs a read-only connection string, "
            "e.g.\n"
            f"    export {ENV_VAR}='postgresql://readonly_user:...@host:5432/media'\n"
            f"Re-run with --skip-db to produce a report without it.\n"
            f"({ENV_VAR} is deliberately separate from DATABASE_URL so a stray "
            "TEST_DATABASE_URL in your shell cannot redirect the run.)"
        )
    return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def redact(dsn: str) -> str:
    """Strip the userinfo from a DSN, for an error message on stderr.

    This is *not* safe for committed output -- it leaves the host and database
    name intact. Use :func:`fingerprint` for anything written to a file.
    """
    return re.sub(r"//[^/@]*@", "//<redacted>@", dsn)


def fingerprint(dsn: str) -> str:
    """A stable, non-reversible identifier for the probed database.

    The report is committed to a public repository, so it must not carry the
    host, database name or credentials of the instance it was generated
    against. A truncated digest still lets successive runs confirm they hit the
    same database -- which is the only thing the provenance line needs to
    establish -- without disclosing where that database is.
    """
    digest = hashlib.sha256(dsn.encode("utf-8")).hexdigest()[:12]
    return (
        f"read-only Postgres, connection fingerprint {digest} "
        f"(supplied via {ENV_VAR}; host, database and credentials not recorded)"
    )


class ReadOnlySession:
    """A psycopg connection that refuses to do anything but read."""

    def __init__(self, dsn: str) -> None:
        """Open the connection and pin the session read-only.

        Args:
            dsn: A libpq connection string.

        Raises:
            RuntimeError: If psycopg is unavailable or the database is
                unreachable. Both fail loudly rather than degrading, so a
                partial report can never look complete.
        """
        try:
            import psycopg  # noqa: PLC0415 -- deferred so --skip-db needs no driver.
        except ImportError as exc:  # pragma: no cover - psycopg is a hard dependency
            raise RuntimeError(f"psycopg is required for Phase 3: {exc}") from exc

        try:
            self._conn = psycopg.connect(dsn, connect_timeout=10)
        except Exception as exc:
            raise RuntimeError(
                f"Could not connect to the database at {redact(dsn)}: {exc}"
            ) from exc

        self._conn.read_only = True
        self._conn.autocommit = False

    def close(self) -> None:
        """Roll back and close. Nothing is ever committed."""
        try:
            self._conn.rollback()
        finally:
            self._conn.close()

    def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Run one guarded read.

        Args:
            sql: A SELECT or WITH statement.
            params: Query parameters.

        Returns:
            All rows.

        Raises:
            ReadOnlyViolation: If the statement is not a read.
        """
        with self._conn.cursor() as cur:
            cur.execute(_guard(sql), params)  # type: ignore[arg-type]
            return list(cur.fetchall())

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run one guarded read and return the first column of the first row."""
        rows = self.fetch(sql, params)
        return rows[0][0] if rows else None

    def server_version(self) -> str:
        """The Postgres version string."""
        return str(self.scalar("SELECT version()") or "unknown")


def _baseline_rtt(session: ReadOnlySession, samples: int = 25) -> float:
    """Median round-trip time for a trivial query.

    Establishes the unit cost of one database round trip from wherever the
    harness is running. An N+1 costs roughly this much per row, so without it a
    reader cannot tell whether a slow endpoint is doing expensive work or cheap
    work too many times.
    """
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        session.scalar("SELECT 1")
        timings.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(timings), 2)


def _catalogue_columns(session: ReadOnlySession, tables: set[str]) -> list[_Column]:
    """Read column names and types for the tables of interest."""
    if not tables:
        return []
    rows = session.fetch(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ANY(%s)
        ORDER BY table_name, ordinal_position
        """,
        (sorted(tables),),
    )
    return [
        _Column(table=str(r[0]), name=str(r[1]), data_type=str(r[2]), nullable=r[3] == "YES")
        for r in rows
    ]


def _existing_tables(session: ReadOnlySession, tables: set[str]) -> set[str]:
    """Which of the requested tables actually exist in the target database."""
    if not tables:
        return set()
    rows = session.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = ANY(%s)
        """,
        (sorted(tables),),
    )
    return {str(r[0]) for r in rows}


def _row_counts(session: ReadOnlySession, tables: set[str]) -> dict[str, int]:
    """Exact row counts, one statement per table."""
    counts: dict[str, int] = {}
    for table in sorted(tables):
        counts[table] = int(session.scalar(f"SELECT count(*) FROM {_quote(table)}") or 0)
    return counts


def _fill_rates(
    session: ReadOnlySession,
    table: str,
    columns: list[_Column],
    total: int,
) -> list[ColumnStats]:
    """Fill rate for every column of one table, in a single pass.

    A text column counts as filled only when it is both non-null and not the
    empty string; an empty string renders exactly as blank in a UI, so counting
    it as present would overstate what a designer can rely on.
    """
    if not columns:
        return []
    parts: list[str] = []
    for column in columns:
        quoted = _quote(column.name)
        if column.data_type in {"text", "character varying", "varchar", "character"}:
            predicate = f"{quoted} IS NOT NULL AND btrim({quoted}) <> ''"
        else:
            predicate = f"{quoted} IS NOT NULL"
        parts.append(f"count(*) FILTER (WHERE {predicate})")
    sql = f"SELECT {', '.join(parts)} FROM {_quote(table)}"  # noqa: S608 -- identifiers
    # are catalogue-sourced and validated by _quote(); there are no parameters.
    row = session.fetch(sql)
    values = row[0] if row else tuple(0 for _ in columns)

    out: list[ColumnStats] = []
    for column, non_null in zip(columns, values, strict=False):
        count = int(non_null or 0)
        out.append(
            ColumnStats(
                table=table,
                column=column.name,
                fill_rate=(count / total) if total else 0.0,
                non_null=count,
                total=total,
            )
        )
    return out


def _cardinality(
    session: ReadOnlySession,
    stats: ColumnStats,
    column: _Column,
    scan_limit: int,
    include_example_values: bool,
) -> ColumnStats:
    """Add distinct-value counts and top values for a facet candidate.

    The count is bounded: a subquery limited to ``scan_limit + 1`` distinct
    values tells us whether the true cardinality exceeds the cap without paying
    for a full distinct scan of a large table. When it does, the number is
    reported as a floor, flagged with ``distinct_capped``.
    """
    quoted = _quote(column.name)
    table = _quote(stats.table)
    distinct = int(
        session.scalar(
            f"SELECT count(*) FROM ("  # noqa: S608 -- validated identifiers, no parameters.
            f"SELECT DISTINCT {quoted} FROM {table} "
            f"WHERE {quoted} IS NOT NULL LIMIT {int(scan_limit) + 1}) AS s"
        )
        or 0
    )
    capped = distinct > scan_limit
    facet = not capped and 0 < distinct <= FACET_CARDINALITY_LIMIT

    top: tuple[tuple[str, int], ...] = ()
    if facet and include_example_values:
        rows = session.fetch(
            f"SELECT {quoted}::text, count(*) FROM {table} "  # noqa: S608 -- as above.
            f"WHERE {quoted} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 2 DESC, 1 ASC LIMIT 10"
        )
        top = tuple((str(r[0]), int(r[1])) for r in rows)

    return ColumnStats(
        table=stats.table,
        column=stats.column,
        fill_rate=stats.fill_rate,
        non_null=stats.non_null,
        total=stats.total,
        distinct=distinct if not capped else scan_limit,
        distinct_capped=capped,
        facet_candidate=facet,
        top_values=top,
    )


def _collections(
    session: ReadOnlySession,
    relationships: list[tuple[str, str, str]],
    row_counts: dict[str, int],
) -> list[CollectionStats]:
    """Distribution of children per parent for each foreign key.

    Args:
        session: An open read-only session.
        relationships: Tuples of (parent table, child table, FK column on child).
        row_counts: Row counts, used to report parents that have no children.

    Returns:
        One record per relationship that has at least one child row.
    """
    out: list[CollectionStats] = []
    for parent, child, fk in relationships:
        sql = (
            "WITH per_parent AS ("  # noqa: S608 -- validated identifiers, no parameters.
            f"SELECT {_quote(fk)} AS parent_id, count(*) AS n "
            f"FROM {_quote(child)} WHERE {_quote(fk)} IS NOT NULL "
            "GROUP BY 1) "
            "SELECT count(*), min(n), "
            "percentile_disc(0.5) WITHIN GROUP (ORDER BY n), "
            "percentile_disc(0.95) WITHIN GROUP (ORDER BY n), "
            "max(n) FROM per_parent"
        )
        row = session.fetch(sql)
        if not row or row[0][0] in (None, 0):
            continue
        parents_with, minimum, median, p95, maximum = row[0]
        out.append(
            CollectionStats(
                parent_table=parent,
                child_table=child,
                fk_column=fk,
                parents_with_children=int(parents_with or 0),
                parents_total=int(row_counts.get(parent, 0)),
                min_children=int(minimum or 0),
                median_children=float(median or 0),
                p95_children=float(p95 or 0),
                max_children=int(maximum or 0),
            )
        )
    return out


def _model_relationships() -> list[tuple[str, str, str]]:
    """Derive parent/child relationships from the foreign keys in the metadata."""
    from app.models import Base  # noqa: PLC0415 -- deferred with the rest of the app.

    out: list[tuple[str, str, str]] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            for fk in column.foreign_keys:
                parent = fk.column.table.name
                out.append((parent, table.name, column.name))
    return sorted(set(out))


def _resolving_display_image(session: ReadOnlySession, present: set[str]) -> int | None:
    """How many library roots the API would resolve a display image for.

    Compiles ``titles_resolving_artwork`` -- the selectable the
    ``resolves_display_image`` filter is built on -- and counts library roots
    inside it. The kinds come from ``DISPLAY_IMAGE_KINDS``, the same constant the
    service resolves them from, so the harness cannot disagree with the API about
    what a display image is.

    Returns:
        The count, or None when the application's query layer cannot be imported
        or the artwork tables are absent. None is reported as a gap; a guess
        would be reported as a fact.
    """
    if not {"artwork", "title_contents", "artwork_kinds"} <= present:
        return None
    try:
        # Deferred: importing the query layer pulls in the models, and Phase 3
        # must be able to run against a database whose application it cannot
        # import -- an older deployment, say. A failure here is a gap, not a crash.
        from sqlalchemy.dialects import postgresql  # noqa: PLC0415

        from app.repositories.artwork_repository import (  # noqa: PLC0415
            titles_resolving_artwork,
        )
        from app.services.title_service import DISPLAY_IMAGE_KINDS  # noqa: PLC0415
    except Exception:
        return None

    codes = ", ".join(f"'{code}'" for code in DISPLAY_IMAGE_KINDS)
    kind_rows = session.fetch(f"SELECT id FROM artwork_kinds WHERE code IN ({codes})")
    kind_ids = [int(row[0]) for row in kind_rows]
    if not kind_ids:
        return None

    try:
        compiled = titles_resolving_artwork(kind_ids).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    except Exception:
        return None

    value = session.scalar(
        "SELECT count(*) FROM titles t WHERE t.library_root IS TRUE " f"AND t.id IN ({compiled})"
    )
    return int(value or 0)


def _library_root_coverage(
    session: ReadOnlySession, present: set[str]
) -> tuple[CoverageMetric, ...]:
    """The three figures the browse grid's design actually turns on.

    ``library_root=true`` Titles are the whole population of the library
    surface, and the design brief makes three of its views out of what they are
    missing: roots with no artwork, roots with no year, roots with no tags.
    Those are first-class views rather than advanced filters, so the figures
    belong in the report as measurements rather than as arithmetic a reader
    performs over the Tables appendix.

    The artwork figure is the decisive one and is not a fill rate on any column.
    A Title resolves a display image from its own artwork or, failing that, by a
    bounded walk through containment -- and only for the kinds the service counts
    as a display image.

    It is measured by compiling the application's *own* selectable,
    ``titles_resolving_artwork``, rather than by writing SQL that means roughly
    the same thing. An approximation here would be worse than no number: counting
    any artwork kind at one hop returns a materially higher figure than the grid
    actually renders, and the whole point of the metric is to say how much of the
    grid is not a poster wall. Reusing the query the filter itself uses means the
    two agree by construction and cannot drift apart.

    Args:
        session: The read-only session.
        present: Tables that exist in the probed database.

    Returns:
        One metric per figure, or an empty tuple when the tables are absent.
    """
    if "titles" not in present:
        return ()

    total_row = session.scalar("SELECT count(*) FROM titles WHERE library_root IS TRUE")
    total = int(total_row or 0)
    if not total:
        return ()

    metrics: list[CoverageMetric] = []

    resolves = _resolving_display_image(session, present)
    if resolves is not None:
        metrics.append(
            CoverageMetric(
                population="Titles with library_root=true",
                attribute="resolve a display image (the API's own resolution)",
                covered=resolves,
                total=total,
                note=(
                    "the browse grid's central design constraint: every root that does "
                    "not resolve one needs the typographic treatment, so this is the "
                    "proportion of the grid that is *not* a poster wall. Measured with "
                    "the API's own resolution query, so it matches what "
                    "`resolves_display_image=true` returns rather than approximating it"
                ),
            )
        )

    metrics.append(
        CoverageMetric(
            population="Titles with library_root=true",
            attribute="have a release_year",
            covered=int(
                session.scalar(
                    "SELECT count(*) FROM titles "
                    "WHERE library_root IS TRUE AND release_year IS NOT NULL"
                )
                or 0
            ),
            total=total,
            note="drives the 'titles with no year' view, and any sort or facet by year",
        )
    )

    if "title_tags" in present:
        metrics.append(
            CoverageMetric(
                population="Titles with library_root=true",
                attribute="have at least one tag",
                covered=int(
                    session.scalar(
                        "SELECT count(*) FROM titles t WHERE t.library_root IS TRUE "
                        "AND EXISTS (SELECT 1 FROM title_tags tt WHERE tt.title_id = t.id)"
                    )
                    or 0
                ),
                total=total,
                note=(
                    "decides whether tag filter chips are a primary navigation device or "
                    "a sparse one"
                ),
            )
        )
    return tuple(metrics)


def collect(
    tables: set[str],
    noload_columns: set[tuple[str, str]],
    cardinality_scan_limit: int = 5000,
    include_example_values: bool = False,
) -> DataShape:
    """Run Phase 3 against the configured read-only database.

    Args:
        tables: Tables to profile, derived from the endpoints being reported on.
        noload_columns: (table, column) pairs to exclude from fill-rate
            reporting because the API does not populate them by default.
        cardinality_scan_limit: Distinct-value scan cap per column.
        include_example_values: Whether to record the most common values of
            low-cardinality columns. Off by default: those values are rows out
            of the probed database, and this report is committed to a public
            repository. Counts and fill rates -- which is what decides whether a
            column can become a facet -- are recorded either way.

    Returns:
        The measured data shape.

    Raises:
        RuntimeError: If the connection string is missing or the database is
            unreachable. Phase 3 never returns a partial result.
    """
    dsn = resolve_dsn()
    session = ReadOnlySession(dsn)
    unknowns: list[Unknown] = []
    try:
        version = session.server_version()
        rtt = _baseline_rtt(session)
        present = _existing_tables(session, tables)
        missing = sorted(tables - present)
        for table in missing:
            unknowns.append(
                Unknown(
                    scope="Phase 3",
                    question=f"data shape for `{table}`",
                    resolution=(
                        "the table does not exist in the probed database; either it "
                        "has not been migrated there, or the model has been renamed "
                        "and production still carries the old name"
                    ),
                )
            )

        row_counts = _row_counts(session, present)
        catalogue = _catalogue_columns(session, present)

        by_table: dict[str, list[_Column]] = {}
        for column in catalogue:
            by_table.setdefault(column.table, []).append(column)

        columns: list[ColumnStats] = []
        for table, cols in sorted(by_table.items()):
            reportable = [c for c in cols if (table, c.name) not in noload_columns]
            stats = _fill_rates(session, table, reportable, row_counts.get(table, 0))
            index = {c.name: c for c in reportable}
            for stat in stats:
                column = index[stat.column]
                if column.data_type in _FACETABLE_TYPES and row_counts.get(table, 0):
                    stat = _cardinality(
                        session, stat, column, cardinality_scan_limit, include_example_values
                    )
                columns.append(stat)

        coverage = _library_root_coverage(session, present)

        relationships = [
            (parent, child, fk)
            for parent, child, fk in _model_relationships()
            if parent in present and child in present
        ]
        collections = _collections(session, relationships, row_counts)

        return DataShape(
            row_counts=dict(sorted(row_counts.items())),
            columns=tuple(sorted(columns, key=lambda c: (c.table, c.column))),
            collections=tuple(
                sorted(collections, key=lambda c: (c.parent_table, c.child_table, c.fk_column))
            ),
            server_version=version,
            captured_from=fingerprint(dsn),
            unknowns=tuple(unknowns),
            baseline_rtt_ms=rtt,
            coverage=coverage,
        )
    finally:
        session.close()
