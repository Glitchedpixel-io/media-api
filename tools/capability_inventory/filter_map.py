"""Phase 2 -- declared filter resolutions, for filters static analysis cannot follow.

The tracer in :mod:`annotate` resolves a filter by finding an ``if params.<name>``
branch in a repository's list method and reducing the ``where()`` predicate under it
to a column. That works for the great majority of filters and is the right default:
it derives the answer from the code rather than trusting a note about the code.

It cannot work for two shapes, and no amount of cleverness makes it:

**A parameter the repository never sees.** ``kind`` arrives as a public code and the
service resolves it to ids before the query is built, so the repository filters on
``artwork_kind_id`` and the string ``params.kind`` appears nowhere in it. Following
that needs dataflow across the router/service/repository boundary through a rename,
which is exactly the kind of inference that is confidently wrong the first time a
layer is refactored.

**A predicate whose expression cannot be matched textually.** ``filename_ext`` is
written against :func:`app.models.asset.filename_extension` so that it matches
``ix_assets_filename_ext``. The model documents, at length, that SQLAlchemy renders
that expression differently from how PostgreSQL stores it and that the two never
match as text -- which is the same reason this module does not try.

So those filters are *declared* here rather than derived, and every declaration is
verified against the live inventory before the report is written: an endpoint that
does not exist, a parameter that endpoint does not accept, or an index no longer in
the schema is a hard error, not a quietly stale line. A declaration says only what
the tracer could not establish; index coverage is still judged by the oracle from the
column and operator given here, so a declaration cannot assert that something is fast.

A third case is not a resolution at all: ``GET /api/inbox``'s ``depth`` walks the
filesystem and has no column behind it. Asking which column it hits is a malformed
question, so it is declared ``not-a-database-filter`` and reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: Shipped declarations, beside this module.
DEFAULT_FILTER_MAP = Path(__file__).with_name("filters.yaml")

#: A filter that exists but is not served by the database at all.
NOT_A_DATABASE_FILTER = "not-a-database-filter"


@dataclass(frozen=True)
class FilterDeclaration:
    """One filter whose resolution is declared rather than derived.

    Attributes:
        endpoint: ``METHOD /path``, matching :attr:`RouteSurface.key`.
        param: Query parameter name, as the route declares it.
        established_by: Why the tracer cannot see this and what holds it true
            instead. Required -- a declaration without a reason is a guess.
        table: Table the filter applies to, or None when not a database filter.
        column: Column it applies to, or None when not a database filter.
        operator: Normalised operator, as :meth:`IndexLookup.judge` spells it.
        expression: How the predicate is actually written, when it is not a bare
            column reference. Reported instead of ``column`` where present.
        index: An index asserted to serve it, for the one case where the
            expression cannot be matched to the inventory by shape.
        constrained: Other columns of ``table`` the same query always pins. A
            composite index serves a column that is not its first only when every
            column before it is constrained too, so omitting these reports a
            covered filter as a sequential scan -- which is how the nested artwork
            reads, which always pin ``entity_type`` and ``entity_id``, would
            otherwise be judged.
        kind: :data:`NOT_A_DATABASE_FILTER`, or None for an ordinary declaration.
    """

    endpoint: str
    param: str
    established_by: str
    table: str | None = None
    column: str | None = None
    operator: str | None = None
    expression: str | None = None
    index: str | None = None
    constrained: tuple[str, ...] = ()
    kind: str | None = None

    @property
    def is_database_filter(self) -> bool:
        """Whether this declaration resolves to a column at all."""
        return self.kind != NOT_A_DATABASE_FILTER


class FilterMapError(ValueError):
    """A declaration file that is malformed, or that no longer matches the code."""


def load(path: Path | None = None) -> dict[tuple[str, str], FilterDeclaration]:
    """Read the declarations, keyed by (endpoint, parameter).

    Keyed by endpoint as well as parameter on purpose. ``kind`` is a filter on
    three separate artwork routes; keyed by name alone a declaration would become a
    global alias and would silently answer for some unrelated future ``kind`` on an
    endpoint nobody checked.

    Args:
        path: File to read. Defaults to :data:`DEFAULT_FILTER_MAP`.

    Returns:
        Declarations keyed by ``(endpoint, param)``.

    Raises:
        FilterMapError: If the file is malformed, or a declaration is incomplete.
    """
    path = path or DEFAULT_FILTER_MAP
    if not path.exists():
        raise FilterMapError(f"filter map not found: {path}")

    raw: Any = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise FilterMapError(f"{path}: top level must be a mapping")

    entries = raw.get("declarations") or []
    if not isinstance(entries, list):
        raise FilterMapError(f"{path}: `declarations` must be a list")

    out: dict[tuple[str, str], FilterDeclaration] = {}
    for position, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise FilterMapError(f"{path}: declaration {position} is not a mapping")

        missing = [
            field for field in ("endpoint", "param", "established_by") if not entry.get(field)
        ]
        if missing:
            raise FilterMapError(
                f"{path}: declaration {position} is missing {', '.join(missing)}. "
                "Every declaration must say which endpoint and parameter it covers, "
                "and why the tracer cannot establish it."
            )

        declaration = FilterDeclaration(
            endpoint=str(entry["endpoint"]),
            param=str(entry["param"]),
            established_by=" ".join(str(entry["established_by"]).split()),
            table=entry.get("table"),
            column=entry.get("column"),
            operator=entry.get("operator"),
            expression=entry.get("expression"),
            index=entry.get("index"),
            constrained=tuple(entry.get("constrained") or ()),
            kind=entry.get("kind"),
        )

        if declaration.kind not in (None, NOT_A_DATABASE_FILTER):
            raise FilterMapError(
                f"{path}: declaration {position} has kind {declaration.kind!r}; the only "
                f"recognised kind is {NOT_A_DATABASE_FILTER!r}"
            )

        if declaration.is_database_filter and not (declaration.table and declaration.column):
            raise FilterMapError(
                f"{path}: declaration {position} ({declaration.endpoint} "
                f"`{declaration.param}`) must give a table and column, or be marked "
                f"kind: {NOT_A_DATABASE_FILTER}"
            )

        key = (declaration.endpoint, declaration.param)
        if key in out:
            raise FilterMapError(
                f"{path}: {declaration.endpoint} `{declaration.param}` is declared twice"
            )
        out[key] = declaration

    return out


def verify(
    declarations: dict[tuple[str, str], FilterDeclaration],
    endpoint_params: dict[str, set[str]],
    index_names: set[str],
) -> None:
    """Check every declaration still matches the code, and raise if one does not.

    A declaration is a standing claim about a codebase that moves underneath it. The
    failure that matters is the silent one: a filter is renamed, the declaration stops
    applying, and the report goes on printing a resolution for a parameter that no
    longer exists. So a declaration that cannot be matched is an error and stops the
    run -- the same stance the harness takes for a probe whose variable will not
    resolve.

    Args:
        declarations: Loaded declarations, as returned by :func:`load`.
        endpoint_params: Query parameter names accepted by each endpoint key.
        index_names: Every index name in the live inventory.

    Raises:
        FilterMapError: If any declaration names something that no longer exists.
    """
    problems: list[str] = []

    for (endpoint, param), declaration in sorted(declarations.items()):
        if endpoint not in endpoint_params:
            problems.append(
                f"{endpoint} `{param}`: no such endpoint in the surface. It was probably "
                "renamed or removed; update or drop this declaration."
            )
            continue
        if param not in endpoint_params[endpoint]:
            problems.append(
                f"{endpoint} `{param}`: the endpoint does not accept a `{param}` query "
                f"parameter. It accepts: {', '.join(sorted(endpoint_params[endpoint])) or 'none'}."
            )
            continue
        if declaration.index and declaration.index not in index_names:
            problems.append(
                f"{endpoint} `{param}`: declares index `{declaration.index}`, which is not "
                "in the live inventory. If the index was dropped, this filter is no longer "
                "covered and the declaration is asserting something false."
            )

    if problems:
        raise FilterMapError(
            "the filter map no longer matches the code:\n  - " + "\n  - ".join(problems)
        )
