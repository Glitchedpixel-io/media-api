"""Turn measurements into risks and a UI verdict.

The verdict line is the point of the report, so the reasoning that produces it
lives in one auditable place rather than being scattered through the renderer.

Every rule here fires on a *measured or read* fact -- a declared cap, a
relationship strategy, a row count, a latency percentile -- never on a hunch. If
the facts needed for a judgement are absent (Phase 3 or Phase 4 skipped), the
verdict says what is missing instead of guessing, and the endpoint is counted in
the Gaps section.
"""

from __future__ import annotations

from .models import DataShape, EndpointRecord, ProbeResult, RouteAnnotation, RouteSurface
from .render import _duration

# A collection this size cannot be rendered without virtualisation, whatever the
# latency, so an unpaginated endpoint that can return it is a design problem
# rather than a performance one.
LARGE_COLLECTION = 200

# p95 above this makes an endpoint unsuitable for a keystroke-driven interaction
# (type-ahead, live filter) even when it is fine for a deliberate navigation.
INTERACTIVE_P95_MS = 200.0

# p95 above this is a page-load problem, not a polish problem.
SLOW_P95_MS = 1000.0

# A response this large is a mobile-data problem regardless of how fast it is.
LARGE_PAYLOAD_BYTES = 512 * 1024

# Below this many rows, Postgres sorts a table fast enough that an unindexed
# sort key is not measurably worse than an indexed one. Reporting it as a "full
# scan" at that size is technically true and practically misleading -- it buries
# the defects that are actually costing time. Above it, the warning stands.
LATENT_SORT_ROWS = 100_000


def _bytes(value: int) -> str:
    """Human-readable byte count, so a 15MB response does not read as 15,516KB."""
    if value < 1024:
        return f"{value}B"
    if value < 1024 * 1024:
        return f"{value / 1024:.0f}KB"
    return f"{value / (1024 * 1024):.1f}MB"


# How much of the per-row cost of a genuine N+1 must be absent before a static
# flag is treated as contradicted. A real query-per-row at the measured round
# trip adds `rtt` milliseconds for every extra row; observing less than this
# fraction of that means the queries are not being issued per row, whatever the
# comprehension around them looks like.
_SCALING_TOLERANCE = 0.10


def _scaling_contradiction(probes: tuple[ProbeResult, ...], rtt: float | None) -> str | None:
    """Whether timings across two page sizes rule out a per-row query.

    A comprehension containing a query is what the static pass can see, and it
    cannot see whether the comprehension runs once or once per row. When two
    probes of the same endpoint differ only in row count, the answer is
    arithmetic: a query issued per row costs about one round trip per row, so
    150 extra rows against a 25ms round trip would add nearly four seconds. If
    the measured difference is a small fraction of that, the queries are being
    batched and the flag is wrong.

    Reporting it anyway is not harmlessly conservative. A report that cries wolf
    gets skimmed, and the findings that matter get skimmed with it.

    Args:
        probes: Every probe attached to the endpoint.
        rtt: Median round-trip time to the database, from Phase 3.

    Returns:
        A sentence naming the probe pair and the arithmetic, or None when the
        measurements do not settle it.
    """
    if not rtt:
        return None
    sized = [
        (p.item_count, p.timing.p50_ms, p.name)
        for p in probes
        if p.measured and p.timing is not None and p.item_count
    ]
    if len(sized) < 2:
        return None
    low = min(sized, key=lambda s: s[0])
    high = max(sized, key=lambda s: s[0])
    extra_rows = high[0] - low[0]
    if extra_rows <= 0:
        return None
    predicted = extra_rows * rtt
    observed = high[1] - low[1]
    if observed >= predicted * _SCALING_TOLERANCE:
        return None
    return (
        f"`{low[2]}` returns {low[0]} rows at p50 {low[1]:.0f}ms and `{high[2]}` "
        f"returns {high[0]} at p50 {high[1]:.0f}ms — {observed:.0f}ms for "
        f"{extra_rows} extra rows, against the ~{predicted / 1000:.1f}s a genuine "
        f"query-per-row would cost at the measured {rtt:.0f}ms round trip. The "
        "queries inside these comprehensions are issued once for the page, not "
        "once per row."
    )


def _worst_probe(probes: tuple[ProbeResult, ...]) -> ProbeResult | None:
    """The slowest successful probe for an endpoint."""
    timed = [p for p in probes if p.measured and p.timing is not None]
    if not timed:
        return None
    return max(timed, key=lambda p: p.timing.p95_ms if p.timing else 0.0)


def _collection_ceiling(
    surface: RouteSurface,
    annotation: RouteAnnotation,
    shape: DataShape | None,
    probes: tuple[ProbeResult, ...] = (),
) -> tuple[int | None, str | None]:
    """How large the unpaginated collection behind an endpoint can get.

    Three sources, strictly ordered, because getting the order wrong produces a
    confidently wrong verdict:

    1. **What a probe actually received.** If a request to this endpoint came
       back with 65,745 items, that is the size of the response -- no inference
       required, and no inference may override it.
    2. **The table's row count**, when the route takes no path parameter. An
       endpoint like ``GET /api/streams`` is not scoped to a parent; it returns
       the table. Reaching for a per-parent relationship here is what made an
       earlier version of this function call a 15MB response "bounded at 79
       rows" -- the 79 was streams *per asset*, which this route never applies.
    3. **The largest per-parent collection**, and only for a route that is
       scoped by a path parameter, where that genuinely is the bound.

    Returns:
        A tuple of (largest collection, where the number came from), or
        (None, None) when nothing establishes it.
    """
    observed = [p.item_count for p in probes if p.measured and p.item_count is not None]
    if observed:
        return max(observed), "measured directly from a probe response"

    if shape is None:
        return None, None

    tables = {t for query in annotation.queries for t in query.tables}
    scoped = any(p.location == "path" for p in surface.params)

    if not scoped:
        best: tuple[int, str] | None = None
        for table in sorted(tables):
            count = shape.row_counts.get(table)
            if count is not None and (best is None or count > best[0]):
                best = (count, f"all {count:,} rows of `{table}`")
        return (best[0], best[1]) if best else (None, None)

    # The collection has to be the one the route actually narrows on. Matching on the
    # child table alone picks the largest per-parent relationship that happens to touch
    # any table in the query -- including a table joined only for an existence check --
    # and reports it as this endpoint's ceiling. That produced a "727 rows, NOT SAFE"
    # verdict for /titles/{id}/references (the number was titles-per-title-type; the
    # table holds none) and a reassuring "17 rows, safe" for
    # /transform_requests/{id}/logs (requests-per-asset, a different relationship).
    narrowed = {(c.table, c.column) for c in annotation.coverage if c.table and c.column}

    best_child: tuple[int, str] | None = None
    for collection in shape.collections:
        if (collection.child_table, collection.fk_column) not in narrowed:
            continue
        if best_child is None or collection.max_children > best_child[0]:
            best_child = (
                collection.max_children,
                f"{collection.child_table}.{collection.fk_column} -> {collection.parent_table}",
            )
    if best_child is not None:
        return best_child

    # No measured relationship matches what the route filters on. The row count of the
    # table being narrowed is still a true upper bound, so prefer it over an unrelated
    # relationship -- but only when the route makes clear which table that is.
    narrowed_tables = {table for table, _ in narrowed if table in tables}
    if len(narrowed_tables) == 1:
        table = narrowed_tables.pop()
        count = shape.row_counts.get(table)
        if count is not None:
            return count, f"at most all {count:,} rows of `{table}`"

    # Anything else is a guess. UNKNOWN sends the reader to measure it; a number from
    # the wrong relationship reads as an answer.
    return None, None


def assess(
    surface: RouteSurface,
    annotation: RouteAnnotation | None,
    probes: tuple[ProbeResult, ...],
    shape: DataShape | None,
) -> tuple[tuple[str, ...], str, str]:
    """Derive risks and a verdict for one endpoint.

    Args:
        surface: Phase 1 record.
        annotation: Phase 2 record, or None if Phase 2 produced nothing.
        probes: Phase 4 results for this endpoint.
        shape: Phase 3 results, or None if the phase was skipped.

    Returns:
        A tuple of (risks, verdict sentence, verdict class). The class is one of
        ``safe``, ``caution``, ``unsafe``, ``write``, ``unknown``.
    """
    risks: list[str] = []
    if annotation is None:
        return (
            ("the handler could not be resolved, so nothing about its cost is known",),
            "the handler could not be analysed; see Gaps.",
            "unknown",
        )

    pagination = annotation.pagination
    worst = _worst_probe(probes)
    measured = worst is not None

    # -- risks -------------------------------------------------------------

    explicit_loops = [q for q in annotation.n_plus_one if not q.owner.endswith("(ORM lazy load)")]
    lazy_loads = [q for q in annotation.n_plus_one if q.owner.endswith("(ORM lazy load)")]

    rtt = shape.baseline_rtt_ms if shape else None
    for query in lazy_loads:
        cap = pagination.max_limit or 0
        cost = (
            f"; at the {rtt:.0f}ms round trip measured against the probed database "
            f"that is about {cap * rtt / 1000:.0f}s of pure latency at the cap"
            if rtt and cap
            else ""
        )
        risks.append(
            f"one extra SELECT per row: {query.owner} is `lazy='select'` and is "
            f"serialised into the response, so a page of {cap or 'N'} rows costs up "
            f"to {cap or 'N'} additional queries against "
            f"`{query.tables[0] if query.tables else '?'}`{cost}"
        )
    if explicit_loops:
        loops = sorted({f"{q.owner} ({q.loop_note})" for q in explicit_loops})
        contradiction = _scaling_contradiction(probes, rtt)
        if contradiction is not None:
            risks.append(
                "static analysis flags queries inside a loop — **contradicted by "
                "measurement** and downgraded: " + "; ".join(loops) + ". " + contradiction
            )
        else:
            risks.append(
                "queries issued inside a loop, so cost grows with the size of the "
                "request: " + "; ".join(loops)
            )

    uncovered_filters = [
        c for c in annotation.coverage if c.role == "filter" and c.covered is False
    ]
    uncovered_sorts = [c for c in annotation.coverage if c.role == "sort" and c.covered is False]
    uncovered_lookups = [
        c for c in annotation.coverage if c.role == "lookup" and c.covered is False
    ]

    if uncovered_sorts:
        names = ", ".join(f"`{c.column}`" for c in uncovered_sorts)
        sorted_table = uncovered_sorts[0].table
        rows = shape.row_counts.get(sorted_table or "") if shape else None
        if rows is not None and rows < LATENT_SORT_ROWS:
            risks.append(
                f"unindexed sort keys ({names}); every page sorts the whole filtered "
                f"set, but `{sorted_table}` holds only {rows:,} rows, so this is "
                "latent rather than live -- the measured cost of an unindexed sort is "
                "currently indistinguishable from an indexed one. It becomes real as "
                "the table grows"
            )
        else:
            risks.append(
                f"unindexed sort keys ({names}); the keyset cursor keeps ordering "
                "correct but every page still sorts the whole filtered set"
            )
    if uncovered_filters:
        names = ", ".join(f"`{c.param}`" for c in uncovered_filters)
        filtered_table = uncovered_filters[0].table
        filtered_rows = shape.row_counts.get(filtered_table or "") if shape else None
        if filtered_rows is not None and filtered_rows < LATENT_SORT_ROWS:
            risks.append(
                f"filters that cannot use an index ({names}); each forces a sequential "
                f"scan, though `{filtered_table}` holds only {filtered_rows:,} rows, so "
                "the scan is currently cheap. This is a constraint on how large the "
                "table can grow before search becomes the bottleneck, not a live cost"
            )
        else:
            risks.append(f"filters that cannot use an index ({names}); each forces a full scan")
    if uncovered_lookups:
        names = ", ".join(f"`{c.table}.{c.column}`" for c in uncovered_lookups)
        risks.append(f"unindexed lookup on {names}; the read is a sequential scan")

    if pagination.style == "offset" and pagination.deep_page_ceiling:
        risks.append(f"deep paging has a hard ceiling: {pagination.deep_page_ceiling}")
    if pagination.stable_under_writes is False:
        risks.append(f"ordering is not stable under concurrent writes: {pagination.stability_note}")

    ceiling, source = (None, None)
    unbounded = pagination.style == "none" and any(
        r.model and r.model.startswith("list[")
        for r in surface.responses
        if r.status == surface.success_status
    )
    if unbounded:
        ceiling, source = _collection_ceiling(surface, annotation, shape, probes)
        if ceiling is None:
            risks.append(
                "no pagination and no page-size cap; the largest possible response "
                "is UNKNOWN without Phase 3"
            )
        elif ceiling >= LARGE_COLLECTION:
            risks.append(
                f"no pagination and no page-size cap; the largest collection measured "
                f"is {ceiling:,} rows ({source})"
            )
        else:
            risks.append(
                f"no pagination, but the largest collection measured is only "
                f"{ceiling:,} rows ({source}), so the absent cap is currently latent"
            )

    if worst and worst.timing:
        if worst.timing.p95_ms >= SLOW_P95_MS:
            risks.append(
                f"measured p95 of {_duration(worst.timing.p95_ms)} on `{worst.name}` is "
                "a page-load-scale wait"
            )
        elif worst.timing.p95_ms >= INTERACTIVE_P95_MS:
            risks.append(
                f"measured p95 of {_duration(worst.timing.p95_ms)} on `{worst.name}` is "
                "too slow to drive from a keystroke"
            )
    largest = max((p.bytes_ or 0 for p in probes if p.measured), default=0)
    if largest >= LARGE_PAYLOAD_BYTES:
        biggest = max(
            (p for p in probes if p.measured and (p.bytes_ or 0) == largest),
            key=lambda p: p.name,
        )
        risks.append(f"largest measured payload is {_bytes(largest)} (`{biggest.name}`)")

    if surface.is_streaming and annotation.filesystem_access:
        risks.append(
            "the response size is bounded by the file on disk, not by anything the "
            "API declares; a client that issues no Range header will be sent the "
            "whole asset"
        )

    failed = [p for p in probes if p.status == "error"]
    for failure in failed:
        risks.append(f"probe `{failure.name}` failed: {failure.reason}")

    if surface.auth.startswith("none"):
        risks.append("no authentication is required on this route")
    if surface.trailing_slash_required:
        risks.append(
            "the trailing slash is required; requesting it without one gets a 307, "
            "which a cross-origin fetch with credentials will not always follow"
        )

    conditional = [
        f.name
        for r in surface.responses
        if r.status == surface.success_status
        for f in r.fields
        if f.conditional_on
    ]
    if conditional:
        risks.append(
            "fields that are empty unless requested, and indistinguishable from "
            "genuinely empty: " + ", ".join(f"`{name}`" for name in sorted(conditional))
        )

    # -- verdict -----------------------------------------------------------

    if surface.method != "GET":
        return tuple(risks), *_write_verdict(surface, annotation, explicit_loops)

    if surface.is_streaming:
        return tuple(risks), *_stream_assessment(probes)

    if pagination.style == "keyset":
        return tuple(risks), *_keyset_verdict(
            pagination, uncovered_sorts, lazy_loads, worst, measured, probes
        )

    if pagination.style == "offset":
        return tuple(risks), *_offset_verdict(pagination, worst, measured)

    if unbounded:
        return tuple(risks), *_unbounded_verdict(ceiling, source, worst, measured)

    return tuple(risks), *_single_verdict(annotation, worst, measured)


def _write_verdict(
    surface: RouteSurface, annotation: RouteAnnotation, loops: list
) -> tuple[str, str]:
    """Verdict text and class for a mutating endpoint.

    The class distinguishes the two kinds of write, because the report treats
    them very differently. A single-row write with no loops and no filesystem
    work has nothing a UI can get wrong beyond ordinary error handling, and gets
    class ``write`` -- the renderer collapses those into one table rather than
    giving each a section that says the same thing. A write that loops, or that
    touches the filesystem as well as the database, has a real failure mode a
    designer has to account for, so it earns a full section and a severity of
    its own.
    """
    if loops:
        return (
            "Work is proportional to the size of the payload, not constant: this "
            "endpoint issues queries per item. Safe from a form submit with a bounded "
            "selection, but send small batches and show determinate progress rather "
            "than a spinner.",
            "caution",
        )
    if annotation.filesystem_access:
        return (
            "Touches the filesystem as well as the database, so it can fail after the "
            "database row already exists. Not safe for optimistic UI — the interface "
            "must wait for the response before showing success, and must be able to "
            "represent a partially-applied result.",
            "caution",
        )
    return (
        "Single-row write with no loops. Safe to drive from a form submit and to treat "
        "optimistically, provided the UI reconciles against the response.",
        "write",
    )


def _stream_assessment(probes: tuple[ProbeResult, ...]) -> tuple[str, str]:
    """Verdict text and class for a streaming endpoint.

    Text and class are produced together on purpose. Deriving them separately
    let them disagree: a run where every probe failed to reach a file produced
    the prose "streaming behaviour was not measured" under a ``NOT SAFE`` tag,
    which reads as a proven defect rather than an absent measurement.

    The distinction that matters is *why* a Range probe did not return 206. If
    no probe reached the endpoint at all -- no media root mounted, no such asset
    -- nothing has been established and the answer is UNKNOWN. Only when the
    endpoint answered and still did not honour a range is it a real finding.
    """
    succeeded = [p for p in probes if p.measured]
    if not succeeded:
        reason = probes[0].reason if probes else None
        detail = f" ({reason})" if reason else ""
        return (
            "streaming behaviour was not measured — no probe reached a file on this "
            f"instance{detail}. Range support is implemented in the service, but "
            "whether it works end to end has not been verified; see Gaps.",
            "unknown",
        )

    ranged = [p for p in succeeded if "206 Partial Content" in " ".join(p.notes)]
    attempted = [p for p in probes if "Range" in " ".join(p.notes)]
    ttfb = next((p.timing.ttfb_p50_ms for p in succeeded if p.timing), None)

    if ranged:
        first = _duration(ttfb) if ttfb is not None else "UNKNOWN"
        return (
            "byte ranges are honoured with 206 and a correct Content-Range, and "
            f"time-to-first-byte is {first}. Safe to point a `<video>` element at "
            "directly — seeking works without buffering the whole file.",
            "safe",
        )
    if attempted:
        return (
            "the endpoint answered, but no Range request returned 206 in this run, so "
            "a scrubber would have to download from the start every time. Not usable "
            "behind a seekable player.",
            "unsafe",
        )
    return (
        "the endpoint streams successfully, but no Range request was probed, so "
        "seeking is unverified; see Gaps.",
        "unknown",
    )


def _keyset_verdict(
    pagination,
    uncovered_sorts: list,
    lazy_loads: list,
    worst: ProbeResult | None,
    measured: bool,
    probes: tuple[ProbeResult, ...] = (),
) -> tuple[str, str]:
    """Verdict text and class for a cursor-paginated list."""
    if not measured:
        return (
            "cursor pagination means deep pages do not degrade the way offset does, "
            "but no timings were taken, so first-screen cost is unmeasured; see Gaps.",
            "unknown",
        )
    p95 = worst.timing.p95_ms if worst and worst.timing else 0.0
    cap = pagination.max_limit or "uncapped"

    if p95 >= SLOW_P95_MS:
        cause = (
            "the per-row lazy load, not the paging" if lazy_loads else "the cost of a single page"
        )
        return (
            f"worst-case p95 is {_duration(p95)}, and the cause is {cause}. Cursor "
            "paging itself holds up: page 400 costs what page 1 does, so infinite "
            "scroll is sound once the per-page cost is fixed.",
            "unsafe",
        )
    if uncovered_sorts or lazy_loads:
        detail = []
        if uncovered_sorts:
            detail.append(
                "restrict the sort control to "
                + ", ".join(f"`{c.column}`" for c in uncovered_sorts if c.covered)
                if any(c.covered for c in uncovered_sorts)
                else "expect the sort control to be the expensive part"
            )
        if lazy_loads:
            detail.append("keep page size well under the cap because of the per-row lazy load")
        return (
            f"the cursor holds up at depth and the cap is {cap}, so this is fine for "
            "first-screen browse and for virtualised infinite scroll. Caveats: "
            + "; ".join(detail)
            + ".",
            "caution",
        )
    return (
        f"cursor paging does not degrade with depth, the page size is capped at {cap}, "
        f"and worst-case p95 is {_duration(p95)}. Suitable as the backing query for a "
        "virtualised full-library scroll.",
        "safe",
    )


def _offset_verdict(pagination, worst: ProbeResult | None, measured: bool) -> tuple[str, str]:
    """Verdict text and class for an offset-paginated list."""
    if not measured:
        return (
            "offset paging over Elasticsearch has a hard result-window ceiling, but "
            "where it falls on this index was not measured; see Gaps.",
            "unknown",
        )
    p95 = worst.timing.p95_ms if worst and worst.timing else 0.0
    return (
        f"fine for a search-results panel showing the first few pages (worst-case p95 "
        f"{_duration(p95)}), and not for anything that scrolls indefinitely: the offset "
        "window is finite and the ordering is by relevance, so a row can move between "
        "pages while the user is reading. Cap the result set in the UI at a few hundred "
        "and offer refinement rather than more pages.",
        "caution",
    )


def _unbounded_verdict(
    ceiling: int | None,
    source: str | None,
    worst: ProbeResult | None,
    measured: bool,
) -> tuple[str, str]:
    """Verdict text and class for an unpaginated collection."""
    if ceiling is None:
        return (
            "this endpoint returns an entire collection with no cap, and the largest "
            "collection in the data was not measured. Do not build a screen on it until "
            "Phase 3 has run; see Gaps.",
            "unknown",
        )
    p95 = worst.timing.p95_ms if worst and worst.timing else None
    timing = f", measured p95 {p95:.0f}ms" if p95 is not None else ""
    if ceiling >= LARGE_COLLECTION:
        return (
            f"the largest collection is {ceiling:,} rows ({source}){timing}, returned in "
            "a single uncapped response. Usable for a count or a preview of the first "
            "few, but a screen that lists these needs either a paginated endpoint or "
            "client-side virtualisation plus the acceptance that the whole payload "
            "crosses the wire first.",
            "unsafe",
        )
    return (
        f"the collection is bounded in practice at {ceiling:,} rows ({source}){timing}, "
        "so it is fine to render directly. The absence of a cap is latent rather than "
        "live — worth a page size before the data grows, not before the UI ships.",
        "safe",
    )


def _single_verdict(
    annotation: RouteAnnotation, worst: ProbeResult | None, measured: bool
) -> tuple[str, str]:
    """Verdict text and class for a single-object read."""
    uncovered = [c for c in annotation.coverage if c.covered is False]
    if not measured:
        if uncovered:
            return (
                "the read is not index-covered, and it was not timed; see Gaps.",
                "unknown",
            )
        return (
            "the read is index-covered, so it is likely fine for a detail view, but it "
            "was not timed; see Gaps.",
            "unknown",
        )
    p95 = worst.timing.p95_ms if worst and worst.timing else 0.0
    if uncovered:
        return (
            f"the lookup is not index-covered and measures p95 {_duration(p95)}, which "
            "will grow with the table. Usable for a deliberate navigation, not for "
            "type-ahead.",
            "caution",
        )
    if p95 >= INTERACTIVE_P95_MS:
        return (
            f"p95 {_duration(p95)} — fine for a detail view, too slow to fire on every "
            "keystroke. Debounce or prefetch.",
            "caution",
        )
    return (
        f"p95 {_duration(p95)}, index-covered. Fine for a detail view and fast enough to "
        "prefetch on hover.",
        "safe",
    )


def apply(
    surface: RouteSurface,
    annotation: RouteAnnotation | None,
    probes: tuple[ProbeResult, ...],
    shape: DataShape | None,
    usage: object | None,
) -> EndpointRecord:
    """Build a fully-assessed endpoint record."""
    risks, verdict, verdict_class = assess(surface, annotation, probes, shape)
    return EndpointRecord(
        surface=surface,
        annotation=annotation,
        probes=probes,
        usage=usage,  # type: ignore[arg-type]
        risks=risks,
        verdict=verdict,
        verdict_class=verdict_class,
    )
