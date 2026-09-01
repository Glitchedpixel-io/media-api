"""Read-only database queries that pick *which* records to fetch.

The API cannot express some of the questions the fixture set needs to ask -- "which
library roots have no release year", "which asset has the most streams", "how deep
does intrinsic containment go". These selectors answer them in SQL and hand back ids;
every record is then fetched through the API, so the fixture is always a real API
response shape.

Nothing here writes. :meth:`Selectors._rows` refuses any statement that is not a
SELECT or a WITH, which is a second line of defence behind the read-only role the
connection is expected to use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import Engine, create_engine, text


@dataclass(frozen=True)
class Measured:
    """A record picked by measurement, with the measurement that picked it.

    Attributes:
        record_id: The chosen record's id.
        measure: The measured value that made it the winner.
    """

    record_id: int
    measure: int


class Selectors:
    """Read-only selection queries against the media database.

    Args:
        database_url: A SQLAlchemy URL for the database to read.
    """

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url)

    def dispose(self) -> None:
        """Close the connection pool."""
        self._engine.dispose()

    def _rows(self, sql: str, **params: Any) -> Sequence[Any]:
        """Run a read-only statement.

        Args:
            sql: The statement. Must be a SELECT or a WITH.
            **params: Bound parameters.

        Returns:
            Sequence[Any]: The result rows.

        Raises:
            ValueError: If the statement is not read-only.
        """
        head = sql.strip().lstrip("(").lstrip().split(None, 1)[0].upper()
        if head not in {"SELECT", "WITH"}:
            raise ValueError(f"refusing non-read-only statement: {head}")
        with self._engine.connect() as conn:
            return conn.execute(text(sql), params).fetchall()

    def scalar(self, sql: str, **params: Any) -> Any:
        """Run a read-only statement and return its first column of its first row.

        Args:
            sql: The statement.
            **params: Bound parameters.

        Returns:
            Any: The scalar value, or None if there were no rows.
        """
        rows = self._rows(sql, **params)
        return rows[0][0] if rows else None

    def ids(self, sql: str, **params: Any) -> list[int]:
        """Run a read-only statement and return its first column as ids.

        Args:
            sql: The statement.
            **params: Bound parameters.

        Returns:
            list[int]: The ids, in the statement's order.
        """
        return [int(row[0]) for row in self._rows(sql, **params)]

    def measured(self, sql: str, **params: Any) -> Measured | None:
        """Run a statement returning ``(id, measure)`` and wrap the first row.

        Args:
            sql: The statement.
            **params: Bound parameters.

        Returns:
            Measured | None: The measured record, or None if there were no rows.
        """
        rows = self._rows(sql, **params)
        if not rows:
            return None
        return Measured(record_id=int(rows[0][0]), measure=int(rows[0][1]))

    # ---------------------------------------------------------------- counts

    def count(self, sql: str, **params: Any) -> int:
        """Run a counting statement.

        Args:
            sql: The statement.
            **params: Bound parameters.

        Returns:
            int: The count.
        """
        return int(self.scalar(sql, **params) or 0)

    # ------------------------------------------------------- library grid

    COUNT_LIBRARY_ROOTS = "SELECT count(*) FROM titles WHERE library_root"

    ROOTS_NO_RELEASE_YEAR = """
        SELECT id FROM titles
        WHERE library_root AND release_year IS NULL
        ORDER BY id
    """

    COUNT_ROOTS_NO_RELEASE_YEAR = """
        SELECT count(*) FROM titles WHERE library_root AND release_year IS NULL
    """

    ROOTS_NO_TAGS = """
        SELECT t.id FROM titles t
        WHERE t.library_root
          AND NOT EXISTS (SELECT 1 FROM title_tags tt WHERE tt.title_id = t.id)
        ORDER BY t.id
    """

    COUNT_ROOTS_NO_TAGS = """
        SELECT count(*) FROM titles t
        WHERE t.library_root
          AND NOT EXISTS (SELECT 1 FROM title_tags tt WHERE tt.title_id = t.id)
    """

    # ------------------------------------------------------------ extremes

    LONGEST_TITLE_NAME = """
        SELECT id, length(name) AS measure FROM titles
        ORDER BY length(name) DESC, id ASC
        LIMIT 1
    """

    LONGEST_SYNOPSIS = """
        SELECT id, length(synopsis) AS measure FROM titles
        WHERE synopsis IS NOT NULL
        ORDER BY length(synopsis) DESC, id ASC
        LIMIT 1
    """

    EMPTY_STRING_SYNOPSIS = """
        SELECT id, 0 AS measure FROM titles
        WHERE synopsis = ''
        ORDER BY id ASC
        LIMIT 1
    """

    NULL_SYNOPSIS = """
        SELECT id, 0 AS measure FROM titles
        WHERE synopsis IS NULL
        ORDER BY id ASC
        LIMIT 1
    """

    COUNT_EMPTY_STRING_SYNOPSIS = "SELECT count(*) FROM titles WHERE synopsis = ''"

    COUNT_NULL_SYNOPSIS = "SELECT count(*) FROM titles WHERE synopsis IS NULL"

    LONGEST_ASSET_PATH = """
        SELECT id, length(path) AS measure FROM assets
        ORDER BY length(path) DESC, id ASC
        LIMIT 1
    """

    LONGEST_ASSET_FILENAME = """
        SELECT id, length(filename) AS measure FROM assets
        ORDER BY length(filename) DESC, id ASC
        LIMIT 1
    """

    MOST_CHILD_TITLES = """
        SELECT parent_title_id, count(*) AS measure FROM title_contents
        WHERE child_title_id IS NOT NULL
        GROUP BY parent_title_id
        ORDER BY measure DESC, parent_title_id ASC
        LIMIT 1
    """

    MOST_ASSETS = """
        SELECT parent_title_id, count(*) AS measure FROM title_contents
        WHERE asset_id IS NOT NULL
        GROUP BY parent_title_id
        ORDER BY measure DESC, parent_title_id ASC
        LIMIT 1
    """

    ASSET_MOST_STREAMS = """
        SELECT asset_id, count(*) AS measure FROM streams
        GROUP BY asset_id
        ORDER BY measure DESC, asset_id ASC
        LIMIT 1
    """

    # The deepest chain of intrinsic containment. Roots of the walk are titles with no
    # intrinsic parent; #90 enforces at most one intrinsic parent per title, so the walk
    # cannot revisit a title, but the path guard is kept so a violation would terminate
    # rather than recurse forever. Ties break on the path array, i.e. lowest ids first.
    DEEPEST_INTRINSIC_CHAIN = """
        WITH RECURSIVE chain AS (
            SELECT t.id AS title_id, 1 AS depth, ARRAY[t.id] AS path
            FROM titles t
            WHERE NOT EXISTS (
                SELECT 1 FROM title_contents tc
                WHERE tc.child_title_id = t.id AND tc.membership = 'intrinsic'
            )
            UNION ALL
            SELECT tc.child_title_id, c.depth + 1, c.path || tc.child_title_id
            FROM chain c
            JOIN title_contents tc
              ON tc.parent_title_id = c.title_id
             AND tc.child_title_id IS NOT NULL
             AND tc.membership = 'intrinsic'
            WHERE NOT tc.child_title_id = ANY(c.path)
        )
        SELECT path, depth FROM chain
        ORDER BY depth DESC, path ASC
        LIMIT 1
    """

    # ---------------------------------------------------- unplaced material

    ASSETS_WITH_NO_TITLE = """
        SELECT a.id FROM assets a
        WHERE NOT EXISTS (SELECT 1 FROM title_contents tc WHERE tc.asset_id = a.id)
        ORDER BY a.id
    """

    COUNT_ASSETS_WITH_NO_TITLE = """
        SELECT count(*) FROM assets a
        WHERE NOT EXISTS (SELECT 1 FROM title_contents tc WHERE tc.asset_id = a.id)
    """

    TITLES_NO_INTRINSIC_PARENT_NOT_ROOT = """
        SELECT t.id FROM titles t
        WHERE NOT t.library_root
          AND NOT EXISTS (
            SELECT 1 FROM title_contents tc
            WHERE tc.child_title_id = t.id AND tc.membership = 'intrinsic'
          )
        ORDER BY t.id
    """

    COUNT_TITLES_NO_INTRINSIC_PARENT_NOT_ROOT = """
        SELECT count(*) FROM titles t
        WHERE NOT t.library_root
          AND NOT EXISTS (
            SELECT 1 FROM title_contents tc
            WHERE tc.child_title_id = t.id AND tc.membership = 'intrinsic'
          )
    """

    COUNT_TITLE_ARTWORK = "SELECT count(*) FROM artwork WHERE entity_type = 'title'"

    # ------------------------------------------------------------ transforms

    FAILED_TRANSFORM_REQUESTS = """
        SELECT id FROM media_transform_requests
        WHERE outcome = 'failed'
        ORDER BY id DESC
    """

    COUNT_FAILED_TRANSFORM_REQUESTS = """
        SELECT count(*) FROM media_transform_requests WHERE outcome = 'failed'
    """

    def deepest_intrinsic_chain(self) -> list[int]:
        """The longest chain of intrinsic containment, root first.

        Returns:
            list[int]: Title ids along the chain, or an empty list if there are none.
        """
        rows = self._rows(self.DEEPEST_INTRINSIC_CHAIN)
        if not rows:
            return []
        return [int(i) for i in rows[0][0]]
