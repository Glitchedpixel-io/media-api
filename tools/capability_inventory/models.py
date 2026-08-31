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
    """An index as declared in the SQLAlchemy metadata or a migration.

    ``method`` matters as much as the columns. ``titles.name`` carries both a btree
    and a GIN trigram index; they are not interchangeable, and the model says so --
    the trigram one cannot serve ``ORDER BY name`` because GIN has no order. Without
    the method the two are indistinguishable here, and whichever happened to be seen
    first won.

    ``ops`` carries the operator classes, because the method alone does not say what a
    GIN index can do. A GIN index over ``jsonb`` serves containment and nothing like a
    ``LIKE``; one declared ``gin_trgm_ops`` serves a leading wildcard. Deciding that
    from the index's *name* would be guessing, which is the failure this whole area
    keeps having.
    """

    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool
    expression: str | None = None
    where: str | None = None
    method: str = "btree"
    ops: tuple[str, ...] = ()
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
class CoverageMetric:
    """What proportion of a designed-for population actually carries something.

    A fill rate over a whole table answers a different question from the one a
    surface asks. The browse grid renders ``library_root=true`` Titles and
    nothing else, so what decides its design is the proportion of *those* that
    resolve a display image -- not the proportion of all Titles, and not a
    figure a reader has to derive by dividing two numbers out of the Tables
    appendix and hoping the denominators match.

    Attributes:
        population: The rows the metric is measured over, in words.
        attribute: What is being counted.
        covered: How many rows have it.
        total: How many rows are in the population.
        note: What the figure means for the design.
    """

    population: str
    attribute: str
    covered: int
    total: int
    note: str = ""

    @property
    def fraction(self) -> float:
        """Covered as a proportion of the population, or 0.0 when empty."""
        return (self.covered / self.total) if self.total else 0.0


@dataclass(frozen=True)
class DataShape:
    """Phase 3 results for the whole database."""

    row_counts: dict[str, int]
    columns: tuple[ColumnStats, ...]
    collections: tuple[CollectionStats, ...]
    server_version: str
    captured_from: str
    unknowns: tuple[Unknown, ...] = ()
    coverage: tuple[CoverageMetric, ...] = ()
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
    records_failure_mode: bool = False
    """Set when the probe exists to record *how* something fails.

    Such a probe accepts a status that means the endpoint did not do its work, so
    what it times is the failure path. It is reported like any other probe and
    contributes nothing to a verdict."""

    @property
    def measured(self) -> bool:
        """Whether this probe actually exercised the endpoint.

        ``status == "ok"`` only means the response carried a status the probe was
        willing to accept, which is not the same as the endpoint having worked.
        ``search-transcripts-past-window`` accepts 503 in order to record how the
        ``max_result_window`` ceiling surfaces; treating that as a measurement put
        "worst-case p95 3ms" in the summary for an endpoint that had never once
        returned a result.

        A non-2xx status is not disqualifying on its own. A by-scheme lookup that
        misses runs the same query as one that hits, and an unsatisfiable range is
        the endpoint behaving correctly -- both are real measurements. What
        disqualifies a probe is a server error, or its own declaration that it is
        recording a failure mode.
        """
        if self.status != "ok" or self.http_status is None:
            return False
        if self.records_failure_mode:
            return False
        return self.http_status < 500


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
# Phase 6 -- write semantics
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldContract:
    """One field of a write endpoint's request body.

    Attributes:
        name: Field name as the client sends it.
        type_: Rendered type.
        required: Whether the body is rejected without it.
        nullable: Whether the schema permits an explicit null.
        default: Declared default, or None when there isn't one.
        omitted_means: What the handler does when the field is absent --
            ``unchanged``, ``set to null``, or ``n/a`` on a create.
        null_means: What an explicit ``null`` does, which is not always the
            same thing. Under ``exclude_none=True`` a null is discarded
            alongside an omission, so a nullable field cannot be cleared
            through that route at all. None on a create.
        constraints: Declared validation bounds (``maxLength``, ``ge``,
            ``pattern``, enum membership...).
        server_controlled: Set when the value is assigned by the server and a
            submitted one is ignored.
    """

    name: str
    type_: str
    required: bool
    nullable: bool
    default: Any = None
    omitted_means: str = "n/a"
    null_means: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    server_controlled: bool = False


@dataclass(frozen=True)
class ErrorCase:
    """One non-2xx response a route can actually produce.

    Attributes:
        status: HTTP status, as a string to match :class:`ResponseInfo`.
        condition: What triggers it, in the terms a caller can act on.
        body: Shape of the response body.
        usable_message: Whether the body carries something a UI could show a
            user. None when it was not established.
        source: ``probed`` when a request produced it in this run, ``declared``
            when it is read from the route's own ``responses``, ``implicit``
            when FastAPI raises it before the handler is entered.
        note: Anything a front end needs beyond the condition.
    """

    status: str
    condition: str
    body: str
    usable_message: bool | None = None
    source: str = "declared"
    note: str = ""


@dataclass(frozen=True)
class SideEffect:
    """Something a write changes besides its target row.

    ``kind`` is one of ``filesystem``, ``enqueue``, ``cascade`` or ``counter``.
    """

    kind: str
    detail: str


@dataclass(frozen=True)
class DeleteSemantics:
    """What a DELETE destroys, and what it merely detaches.

    Stated in the vocabulary the interface will use, because "remove from this
    collection" and "delete permanently" must never be the same button --
    principle 4 of the design brief, and the reason this record is separate
    from the generic side-effect list.

    Attributes:
        destroys: The object that ceases to exist, or "nothing".
        detaches: The edge that is broken, or "nothing".
        children: ``orphaned``, ``cascaded``, ``blocked`` or ``none``.
        reachable_with_references: Whether the delete succeeds while other rows
            still reference the target. None when not established.
        ui_vocabulary: The label this affordance must carry.
    """

    destroys: str
    detaches: str
    children: str
    reachable_with_references: bool | None = None
    ui_vocabulary: str = ""


@dataclass(frozen=True)
class WriteContract:
    """Everything Phase 6 establishes about one mutating endpoint.

    ``probed`` distinguishes a contract that was exercised against a disposable
    instance from one derived from the code alone. Both are reported; only the
    first can say what a constraint violation actually returns, which is the
    question the error taxonomy exists to answer.
    """

    fields: tuple[FieldContract, ...] = ()
    unknown_fields: str = "UNKNOWN"
    omission_semantics: str = "UNKNOWN"
    idempotency: str = "UNKNOWN"
    idempotency_evidence: str = ""
    atomic: bool | None = None
    atomicity_note: str = ""
    concurrency: str = "UNKNOWN"
    side_effects: tuple[SideEffect, ...] = ()
    delete: DeleteSemantics | None = None
    auth: str = "UNKNOWN"
    audience: str = "UNKNOWN"
    errors: tuple[ErrorCase, ...] = ()
    probed: bool = False
    unknowns: tuple[Unknown, ...] = ()


@dataclass(frozen=True)
class ConstraintMapping:
    """A database constraint a user could violate through the interface.

    The column that matters is ``distinguishable``: a violation that surfaces as
    a generic 500 gives a front end nothing to say to the user and nothing to
    branch on, and no amount of client work recovers it. That is a back-end
    defect, and this record is where it is named.
    """

    name: str
    table: str
    kind: str
    definition: str
    endpoints: tuple[str, ...] = ()
    status: int | None = None
    body: str = ""
    distinguishable: bool | None = None
    ui_message: str = ""
    note: str = ""


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
    write_contract: WriteContract | None = None
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
    constraint_map: tuple[ConstraintMapping, ...] = ()
    notes: tuple[str, ...] = ()
