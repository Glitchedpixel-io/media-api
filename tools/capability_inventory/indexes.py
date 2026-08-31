"""Index inventory, from the models and cross-checked against the migrations.

Two sources are read because they can disagree, and the disagreement matters:

* ``Base.metadata`` is exact -- it is what the ORM believes, including
  expression indexes and partial ``WHERE`` clauses, read from the mappers
  rather than guessed from source text.
* ``alembic/versions/*.py`` is what a freshly migrated database actually gets.

CI runs ``alembic check`` so these should agree, but an index that exists in
production and in neither source (or vice versa) has happened here before --
``ix_external_identifiers_entity`` and ``ix_tags_name_lower`` were both live in
production while absent from the models until migration ``ee9eb74e4b4b`` added
them. Reporting the union, tagged by source, keeps that visible.

Coverage is judged with the *operator* in hand, not just the column. A btree on
``assets.path`` does not help ``path ILIKE 'x%'``: the default opclass is
case-sensitive, so a case-insensitive prefix match cannot use it. Reporting such
a column as "indexed: yes" would be the single most misleading line in the
report.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .models import IndexInfo

# Operators that a plain btree index can serve.
_BTREE_OPERATORS = frozenset({"==", ">=", "<=", ">", "<", "in_", "is_", "is_not", "between"})

# Operators that can never use a plain btree, whatever the column.
_UNINDEXABLE = frozenset(
    {
        "ilike_contains",
        "like_contains",
        "ilike_suffix",
        "like_suffix",
        "any",
        "contains",
    }
)


def _index_method(index: object) -> str:
    """The access method an index uses, defaulting to btree.

    Only ``postgresql_using`` is consulted, which is how the models spell it. The
    default matters: every operator this module judges -- equality, ranges, ordering,
    prefix matching -- is a btree capability, so an index that does not say otherwise
    is treated as one.
    """
    options = getattr(index, "dialect_options", {})
    try:
        using = options["postgresql"]["using"]
    except (KeyError, TypeError):
        return "btree"
    return str(using).lower() if using else "btree"


def _partial_where(index: object) -> str | None:
    """Render an index's partial-index predicate, if it has one.

    A SQLAlchemy clause element raises on ``bool()``, so the predicate has to be
    compared against None explicitly rather than tested for truthiness.
    """
    options = getattr(index, "dialect_options", {})
    try:
        predicate = options["postgresql"]["where"]
    except (KeyError, TypeError):
        return None
    return None if predicate is None else str(predicate)


def from_metadata() -> tuple[IndexInfo, ...]:
    """Read every index and unique constraint from the SQLAlchemy metadata.

    Returns:
        Indexes declared by the models, including the implicit unique indexes
        Postgres creates for primary keys and unique constraints -- those are
        real and usable, so omitting them would under-report coverage.
    """
    from app.models import Base  # noqa: PLC0415 -- deferred: importing the models

    # pulls in app.database, which the harness only wants loaded once the
    # environment defaults from static_surface.load_app() are in place.

    out: list[IndexInfo] = []
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            columns: list[str] = []
            expression: str | None = None
            for expr in index.expressions:
                name = getattr(expr, "name", None)
                if name and getattr(expr, "table", None) is not None:
                    columns.append(str(name))
                else:
                    expression = str(expr)
            out.append(
                IndexInfo(
                    name=index.name or "<unnamed>",
                    table=table.name,
                    columns=tuple(columns),
                    unique=bool(index.unique),
                    expression=expression,
                    where=_partial_where(index),
                    method=_index_method(index),
                    source="models",
                )
            )
        if table.primary_key is not None and table.primary_key.columns:
            out.append(
                IndexInfo(
                    name=f"{table.name}_pkey",
                    table=table.name,
                    columns=tuple(c.name for c in table.primary_key.columns),
                    unique=True,
                    source="primary key",
                )
            )
        for constraint in table.constraints:
            if constraint.__class__.__name__ != "UniqueConstraint":
                continue
            out.append(
                IndexInfo(
                    name=constraint.name or f"uq_{table.name}",
                    table=table.name,
                    columns=tuple(c.name for c in constraint.columns),
                    unique=True,
                    source="unique constraint",
                )
            )
        # A column-level unique=True yields a unique index Postgres can use, which
        # may not appear in table.indexes -- `unique=True` on its own renders as a
        # UniqueConstraint. When `index=True` is also set it *does* appear there,
        # already carrying the name Postgres will use, and synthesising a second
        # entry here invents an index that does not exist. See _deduplicate.
        for column in table.columns:
            if column.unique and not column.primary_key:
                out.append(
                    IndexInfo(
                        name=f"{table.name}_{column.name}_key",
                        table=table.name,
                        columns=(column.name,),
                        unique=True,
                        source="column unique=True",
                    )
                )
    return _deduplicate(out)


# Which source names the index Postgres actually creates, best first. A real Index
# object always does. A column-level `unique=True` renders as `<table>_<col>_key`,
# which is also what Postgres names an *unnamed* UniqueConstraint -- so where both
# describe the same column the constraint entry is the invented one.
_SOURCE_PRECEDENCE = ("models", "column unique=True", "unique constraint", "primary key")


def _deduplicate(entries: list[IndexInfo]) -> tuple[IndexInfo, ...]:
    """Collapse entries that describe one index reached by several routes.

    The three passes above read the same declaration in different ways, so a
    column written ``unique=True, index=True`` produced both ``ix_tags_name`` and a
    synthesised ``tags_name_key``. Only the first exists. Reported side by side
    they read as a duplicated index -- which is how #59 came to propose a migration
    dropping four indexes that were never there.

    Args:
        entries: Everything the metadata passes collected.

    Returns:
        One entry per distinct index, named as Postgres names it.
    """
    best: dict[tuple[str, tuple[str, ...], str | None, str | None, bool, str], IndexInfo] = {}
    for entry in entries:
        key = (
            entry.table,
            entry.columns,
            entry.expression,
            entry.where,
            entry.unique,
            entry.method,
        )
        current = best.get(key)
        if current is None or _SOURCE_PRECEDENCE.index(entry.source) < _SOURCE_PRECEDENCE.index(
            current.source
        ):
            best[key] = entry
    return tuple(sorted(best.values(), key=lambda i: (i.table, i.name)))


def from_migrations(alembic_dir: Path) -> tuple[IndexInfo, ...]:
    """Collect ``op.create_index`` calls across the migration history.

    Args:
        alembic_dir: The ``alembic/versions`` directory.

    Returns:
        Indexes any migration creates. Revision order is not resolved: an index
        created and later dropped still appears, tagged by its migration, which
        is the right behaviour for a cross-check whose job is to surface
        divergence rather than to simulate the final schema.
    """
    out: list[IndexInfo] = []
    if not alembic_dir.is_dir():
        return ()
    for path in sorted(alembic_dir.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "create_index"):
                continue
            args = node.args
            name = _literal(args[0]) if len(args) > 0 else None
            table = _literal(args[1]) if len(args) > 1 else None
            columns: tuple[str, ...] = ()
            expression: str | None = None
            if len(args) > 2 and isinstance(args[2], ast.List):
                literals = [_literal(e) for e in args[2].elts]
                columns = tuple(str(v) for v in literals if v is not None)
                if len(columns) != len(args[2].elts):
                    expression = ast.unparse(args[2])
            unique = any(kw.arg == "unique" and _literal(kw.value) is True for kw in node.keywords)
            out.append(
                IndexInfo(
                    name=str(name or "<computed>"),
                    table=str(table or "<computed>"),
                    columns=columns,
                    unique=unique,
                    expression=expression,
                    source=f"migration {path.stem.split('_', 1)[0]}",
                )
            )
    return tuple(sorted(out, key=lambda i: (i.table, i.name)))


def _literal(node: ast.expr) -> object | None:
    """Best-effort literal evaluation of an AST node.

    ``op.f("ix_foo")`` is unwrapped: Alembic's naming-convention helper is a call,
    not a literal, and leaving it unresolved makes the index name render as
    ``<computed>`` in the report.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "f"
        and node.args
    ):
        node = node.args[0]
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


class IndexLookup:
    """Answers "can this column and operator use an index?"."""

    def __init__(self, indexes: tuple[IndexInfo, ...]) -> None:
        """Build the lookup.

        Args:
            indexes: Indexes describing the *live* schema -- i.e. those read from
                the SQLAlchemy metadata.

        Raises:
            ValueError: If any index came from the migration scan. That scan does
                not resolve revision order, so an index created and later dropped
                or renamed still appears in it; judging coverage against those
                lets a dead object read as live. This is a constructor guard
                rather than a convention because the two collections are merged
                for the report, and passing the merged tuple here is the easy
                mistake to make -- it is the one that shipped.
        """
        historical = sorted({i.source for i in indexes if i.source.startswith("migration ")})
        if historical:
            raise ValueError(
                "IndexLookup must be built from the live schema, but was given indexes "
                f"sourced from {', '.join(historical)}. Pass indexes.from_metadata() "
                "only; the migration scan is a historical cross-check for the report."
            )
        self._indexes = indexes
        self._by_leading: dict[tuple[str, str], list[IndexInfo]] = {}
        self._expressions: dict[str, list[IndexInfo]] = {}
        for index in indexes:
            if index.columns:
                # Only the leading column of a composite index is usable on its
                # own; later columns need the earlier ones constrained too.
                self._by_leading.setdefault((index.table, index.columns[0]), []).append(index)
            if index.expression:
                self._expressions.setdefault(index.table, []).append(index)

    def all(self) -> tuple[IndexInfo, ...]:
        """Every index in the inventory."""
        return self._indexes

    def covering(
        self, table: str, column: str, constrained: frozenset[str] = frozenset()
    ) -> IndexInfo | None:
        """Return an index that can serve a lookup on ``column``, if any.

        A composite index serves a column that is not its first only when every
        column before it is *also* constrained by the same query. Those companion
        constraints do not have to be ``WHERE`` clauses: a join condition pins a
        column just as well, which is how
        ``uq_external_identifier_scheme_id (scheme_id, external_id)`` serves a
        lookup on ``external_id`` in a query that joins ``id_schemes`` on
        ``scheme_id``. Judging the column on its own reports that as a sequential
        scan when the planner does an index scan.

        Partial indexes are never offered. One serves only a query whose own
        predicate implies its ``WHERE`` clause, which this oracle has no way to
        establish, so claiming one would report a sequential scan as an index scan.
        Non-btree indexes are not offered either, for the same reason in a different
        shape: every operator judged here is something only a btree does.

        Args:
            table: Table being read.
            column: Column the predicate applies to.
            constrained: Other columns of ``table`` pinned by the same query,
                from ``WHERE`` clauses and join conditions alike.

        Returns:
            The index the planner can use, or None if no index applies.
        """
        candidates = [
            i
            for i in self._by_leading.get((table, column), [])
            if not i.where and i.method == "btree"
        ]

        for index in self._indexes:
            if index.table != table or column not in index.columns:
                continue
            if index.method != "btree":
                # Every operator judged here -- equality, ranges, ordering, prefix --
                # is a btree capability. `titles.name` carries a GIN trigram index
                # alongside its btree, and the model records why they are not
                # interchangeable: GIN has no order, so it cannot serve `ORDER BY
                # name`. Offering it would name an index the planner cannot use.
                continue
            if index.where:
                # A partial index serves only queries whose own predicate implies
                # its WHERE clause, and nothing here establishes that. Naming one
                # anyway reports a filter as served by an index the planner will not
                # use: `uq_artwork_one_primary_per_kind` is partial on
                # `is_primary IS true`, while `list_for_entity` does not constrain
                # `is_primary` at all -- it orders by it. Skipping them can only
                # understate coverage, which is the safe direction to be wrong in.
                continue
            position = index.columns.index(column)
            if position == 0:
                continue  # already collected above
            if all(earlier in constrained for earlier in index.columns[:position]):
                candidates.append(index)

        if not candidates:
            return None
        # Prefer a single-column index; it is the one the planner will reach for.
        return min(candidates, key=lambda i: (len(i.columns), i.name))

    def expression_index(self, table: str, function: str, column: str) -> IndexInfo | None:
        """Return an expression index applying ``function`` to ``column``.

        The two sources spell the same index differently: the SQLAlchemy
        metadata renders it table-qualified (``lower(tags.name)``) while a
        migration's ``sa.literal_column`` keeps the bare form
        (``lower(name)``). Both are accepted, so an index present in the models
        is not reported as missing because of how it was written down.
        """
        for index in self._expressions.get(table, []):
            if not index.expression:
                continue
            normalised = index.expression.replace(f"{table}.", "").replace(" ", "").lower()
            if normalised == f"{function}({column})".lower():
                return index
        return None

    def judge(
        self,
        table: str | None,
        column: str | None,
        operator: str | None,
        constrained: frozenset[str] = frozenset(),
        function: str | None = None,
    ) -> tuple[bool | None, str | None, str]:
        """Decide whether a filter can use an index.

        Args:
            table: Table the filter applies to, or None if unresolved.
            column: Column the filter applies to, or None if unresolved.
            operator: Normalised operator name, or None if unresolved.
            constrained: Other columns of ``table`` pinned by the same query,
                which decide whether a composite index applies -- see
                :meth:`covering`.
            function: SQL function applied to ``column`` by the predicate, if any.
                When given, only an expression index over that function can serve
                the filter, and an index on the bare column cannot -- so this is
                answered from :meth:`expression_index` and never falls through to
                :meth:`covering`. Letting it fall through would report an index the
                planner cannot use for this query, which is worse than reporting
                none.

        Returns:
            A tuple of (covered, index name, note). ``covered`` is None when the
            harness could not resolve enough to answer, and the note says so --
            it is never silently downgraded to False.
        """
        if table is None or column is None:
            return None, None, "column could not be resolved from the handler"
        if operator is None:
            return None, None, "comparison operator could not be resolved"

        index = self.covering(table, column, constrained)

        if function is not None and operator not in _UNINDEXABLE:
            expression = self.expression_index(table, function, column)
            if expression is not None:
                return (
                    True,
                    expression.name,
                    f"{function}({table}.{column}) served by {expression.name}",
                )
            return (
                False,
                None,
                f"no expression index on {function}({table}.{column}); an index on "
                f"{table}.{column} alone cannot serve a predicate written against "
                f"{function}() of it, so this is a sequential scan",
            )

        if operator in _UNINDEXABLE:
            if operator in {"ilike_contains", "like_contains", "ilike_suffix", "like_suffix"}:
                shape = "substring" if "contains" in operator else "suffix"
                return (
                    False,
                    None,
                    f"{shape} match on {table}.{column}; the pattern has a leading "
                    "wildcard, which a btree cannot use at all, so this is a "
                    "sequential scan regardless of indexing (a trigram index would "
                    "be needed)",
                )
            return (
                False,
                None,
                f"{operator} on {table}.{column} cannot use a plain btree index",
            )

        if operator == "ilike_prefix":
            expr = self.expression_index(table, "lower", column)
            if expr is not None:
                return True, expr.name, f"case-insensitive prefix served by {expr.name}"
            if index is not None:
                return (
                    False,
                    None,
                    f"{index.name} exists on {table}.{column} but the match is "
                    "case-insensitive (ILIKE) and the index uses the default "
                    "case-sensitive opclass, so the planner cannot use it; a "
                    f"lower({column}) expression index or text_pattern_ops would",
                )
            return False, None, f"no index on {table}.{column}; prefix scan is sequential"

        if operator in _BTREE_OPERATORS:
            if index is not None:
                if index.columns and index.columns[0] != column:
                    leading = ", ".join(index.columns[: index.columns.index(column)])
                    return (
                        True,
                        index.name,
                        f"served by {index.name}; the same query pins {leading}, "
                        "so the composite index applies from its leading column",
                    )
                return True, index.name, f"served by {index.name}"
            return False, None, f"no index on {table}.{column}; requires a sequential scan"

        return None, None, f"unrecognised operator {operator!r}"
