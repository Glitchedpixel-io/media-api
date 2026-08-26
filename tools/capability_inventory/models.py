"""Record types for the capability inventory.

Every type here is a frozen dataclass so a completed run is immutable and can be
serialised deterministically. Nothing in this module knows how a value was
obtained -- the phase modules build these records, and :mod:`.render` turns them
into the Markdown and JSON deliverables.

A field typed ``X | None`` means "not established". The renderer prints
``UNKNOWN`` for those and expects a matching :class:`Unknown` entry explaining
what would settle it, so a gap is never silently rendered as a zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Phase 1 -- static surface
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamInfo:
    """A single request parameter declared by a route.

    Attributes:
        name: Parameter name as the client sends it.
        location: One of ``path``, ``query``, ``header``, ``cookie``, ``body``.
        type_: Rendered type, e.g. ``int`` or ``str | None``.
        required: Whether the parameter must be supplied.
        default: Declared default, or None when there isn't one.
        description: Declared description, or None.
        constraints: Declared validation bounds (``ge``, ``le``, ``maxLength``...).
    """

    name: str
    location: str
    type_: str
    required: bool
    default: Any = None
    description: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldInfo:
    """A field of a response model.

    Attributes:
        name: Field name in the serialised response.
        type_: Rendered type.
        nullable: Whether the schema permits null.
        conditional_on: Populated only when the named request parameter asks for
            it. Set for relationship fields loaded with ``lazy="noload"``, which
            serialise as an empty collection rather than being absent when not
            requested. A database fill rate is meaningless for these, so
            :mod:`.data_shape` skips them and the renderer says why.
    """

    name: str
    type_: str
    nullable: bool
    conditional_on: str | None = None


@dataclass(frozen=True)
class ResponseInfo:
    """A declared response for one status code."""

    status: str
    description: str | None
    model: str | None
    fields: tuple[FieldInfo, ...] = ()
    media_type: str | None = None
    row_model: str | None = None
    """The payload schema inside a pagination envelope or a list, when there is
    one. ``PaginatedResponse[AssetReadExtended]`` describes the envelope; this
    is ``AssetReadExtended``, which is the shape a designer actually renders."""


@dataclass(frozen=True)
class RouteSurface:
    """Everything Phase 1 establishes about one operation."""

    method: str
    path: str
    operation_id: str | None
    summary: str | None
    tags: tuple[str, ...]
    auth: str
    handler_module: str
    handler_name: str
    params: tuple[ParamInfo, ...]
    request_body: str | None
    responses: tuple[ResponseInfo, ...]
    success_status: str
    is_streaming: bool
    trailing_slash_required: bool

    @property
    def key(self) -> str:
        """Stable identity used for sorting and for JSON keys."""
        return f"{self.method} {self.path}"


# --------------------------------------------------------------------------
# Phase 2 -- code annotation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexInfo:
    """An index as declared in the SQLAlchemy metadata or a migration."""

    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool
    expression: str | None = None
    where: str | None = None
    source: str = "models"


@dataclass(frozen=True)
class QueryInfo:
    """One database statement issued while serving a request.

    Attributes:
        owner: ``Class.method`` that issues it.
        kind: Statement shape -- ``select``, ``select_page``, ``get``,
            ``update``, ``delete``, ``count``.
        tables: Tables the statement touches, as far as they resolve statically.
        in_loop: True when the call site is lexically inside a ``for``/``while``
            or a comprehension. This is the N+1 signal.
        loop_note: What the loop iterates over, when it resolves.
        writes: Whether the statement mutates data.
        line: Source line of the call site.
        source_file: Repository-relative path of the call site.
    """

    owner: str
    kind: str
    tables: tuple[str, ...]
    in_loop: bool
    loop_note: str | None
    writes: bool
    line: int
    source_file: str


@dataclass(frozen=True)
class FilterCoverage:
    """Whether a filter or sort parameter can use an index.

    ``covered`` is deliberately three-valued. None means the harness could not
    resolve the column or the operator, and a matching :class:`Unknown` says so.
    """

    param: str
    role: str
    table: str | None
    column: str | None
    operator: str | None
    covered: bool | None
    index: str | None
    note: str


@dataclass(frozen=True)
class PaginationInfo:
    """How an endpoint pages, and whether its ordering is stable.

    Attributes:
        style: ``keyset``, ``offset``, or ``none``.
        default_limit: Declared default page size.
        max_limit: Declared cap, or None when uncapped.
        sort_fields: Sort keys the endpoint accepts.
        default_sort: The sort applied when the caller asks for none.
        stable_under_writes: Whether a concurrent insert can cause a row to be
            skipped or repeated across pages.
        stability_note: Why, in one line.
        deep_page_ceiling: Hard ceiling past which paging fails outright.
    """

    style: str
    default_limit: int | None = None
    max_limit: int | None = None
    sort_fields: tuple[str, ...] = ()
    default_sort: str | None = None
    stable_under_writes: bool | None = None
    stability_note: str = ""
    deep_page_ceiling: str | None = None


@dataclass(frozen=True)
class Unknown:
    """Something the harness could not establish.

    Attributes:
        scope: Endpoint key, or a phase name for run-wide gaps.
        question: What is not known.
        resolution: The concrete thing that would settle it.
    """

    scope: str
    question: str
    resolution: str


@dataclass(frozen=True)
class RouteAnnotation:
    """Everything Phase 2 establishes about one operation."""

    service: str | None
    repositories: tuple[str, ...]
    queries: tuple[QueryInfo, ...]
    n_plus_one: tuple[QueryInfo, ...]
    coverage: tuple[FilterCoverage, ...]
    pagination: PaginationInfo
    external_calls: tuple[str, ...]
    background_work: tuple[str, ...]
    hard_limits: tuple[str, ...]
    filesystem_access: tuple[str, ...]
    unknowns: tuple[Unknown, ...]


# --------------------------------------------------------------------------
# Phase 3 -- data shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnStats:
    """Fill rate and cardinality for one column.

    Attributes:
        fill_rate: Fraction of rows that are non-null and, for text, non-empty.
        distinct: Distinct non-null values, or None when not scanned.
        distinct_capped: True when the scan stopped at the configured cap, so
            ``distinct`` is a floor rather than the true cardinality.
        facet_candidate: Whether the cardinality is low enough for chips.
        top_values: Most frequent values, for low-cardinality columns only.
    """

    table: str
    column: str
    fill_rate: float
    non_null: int
    total: int
    distinct: int | None = None
    distinct_capped: bool = False
    facet_candidate: bool = False
    top_values: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class CollectionStats:
    """Distribution of children per parent for one relationship."""

    parent_table: str
    child_table: str
    fk_column: str
    parents_with_children: int
    parents_total: int
    min_children: int
    median_children: float
    p95_children: float
    max_children: int


@dataclass(frozen=True)
class DataShape:
    """Phase 3 results for the whole database."""

    row_counts: dict[str, int]
    columns: tuple[ColumnStats, ...]
    collections: tuple[CollectionStats, ...]
    server_version: str
    captured_from: str
    unknowns: tuple[Unknown, ...] = ()
    baseline_rtt_ms: float | None = None
    """Median round-trip time for a trivial ``SELECT 1``.

    This is the unit cost of an N+1: a query issued once per row costs about
    this much per row, whatever the query does. It is a property of where the
    harness ran relative to the database, not of the API, so it is recorded
    alongside the timings rather than folded into them -- a co-located API sees
    a much smaller number for the same architectural defect."""


# --------------------------------------------------------------------------
# Phase 4 -- timed probes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Timing:
    """Latency percentiles over N runs, warm-up discarded."""

    runs: int
    p50_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    ttfb_p50_ms: float | None = None
    ttfb_p95_ms: float | None = None


@dataclass(frozen=True)
class ProbeResult:
    """One probe from ``probes.yaml``, executed.

    ``status`` is ``ok``, ``skipped``, ``unavailable`` or ``error``. Anything
    other than ``ok`` carries a reason and contributes no timing, so a failed
    probe can never be mistaken for a fast one.
    """

    name: str
    endpoint_key: str
    method: str
    url: str
    status: str
    http_status: int | None = None
    timing: Timing | None = None
    bytes_: int | None = None
    item_count: int | None = None
    reason: str | None = None
    notes: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Phase 5 -- dead surface
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageEvidence:
    """Evidence that an endpoint is or is not called.

    Attributes:
        strength: ``strong`` when a caller was found in a consumer codebase or
            an access log, ``weak`` when the only evidence is in-repository.
        callers: Where references were found.
        test_references: Test files that exercise the route.
    """

    endpoint_key: str
    referenced: bool
    strength: str
    callers: tuple[str, ...]
    test_references: tuple[str, ...]
    note: str


# --------------------------------------------------------------------------
# Assembled record
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointRecord:
    """One endpoint, across every phase that ran."""

    surface: RouteSurface
    annotation: RouteAnnotation | None = None
    probes: tuple[ProbeResult, ...] = ()
    usage: UsageEvidence | None = None
    risks: tuple[str, ...] = ()
    verdict: str = "UNKNOWN"
    verdict_class: str = "unknown"

    @property
    def key(self) -> str:
        return self.surface.key


@dataclass(frozen=True)
class Inventory:
    """A complete run."""

    generated_from: str
    app_version: str
    phases_run: tuple[str, ...]
    phases_skipped: tuple[str, ...]
    endpoints: tuple[EndpointRecord, ...]
    indexes: tuple[IndexInfo, ...]
    data_shape: DataShape | None
    unknowns: tuple[Unknown, ...]
    notes: tuple[str, ...] = ()
