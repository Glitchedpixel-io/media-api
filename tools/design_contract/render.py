"""Render the front-end API contract as Markdown.

Written for a designer who has read the brief and knows nothing about FastAPI.
No commentary about the inventory or about this generator appears in the output.

Density is a hard constraint: the document sits permanently in a design tool's
context. Every endpoint spec is rendered once, at its primary surface, and
cross-referenced elsewhere.
"""

from __future__ import annotations

import re

from .load import Endpoint, Inventory, Timing
from .surfaces import Surface, SurfaceMap

#: Query parameters that drive paging rather than filtering.
PAGING_PARAMS = frozenset({"after", "before", "limit", "sort", "include"})

#: A probe slower than this is called out in the Costly list.
SLOW_MS = 200.0

#: An uncapped collection this large or larger is called out in the Costly list.
#: Below it, a fixed vocabulary of a few rows is not worth a designer's attention.
UNCAPPED_ROWS = 100

#: Error conditions carried by nearly every write. Stated once in the legend so
#: per-endpoint lines can carry only what is specific to that endpoint.
GENERIC_CONDITIONS = frozenset(
    {
        "Not Found - the requested resource does not exist",
        "Conflict - unique constraint violated or relationship not permitted",
        "Unprocessable Entity - validation error or database integrity " "constraint violated",
        "Validation Error",
        "Locked - database is currently in read-only mode",
        "bearer token missing or rejected",
    }
)

#: Provenance the inventory records but a designer does not need.
_PROBE_NOTE = re.compile(r"\s*\((?:Confirmed|Measured|Probed)[^)]*\)")


def _prose(value: str) -> str:
    """Collapse whitespace and drop inventory provenance from a sentence.

    Args:
        value: Raw text from the inventory.

    Returns:
        Text fit for the document.
    """
    return _PROBE_NOTE.sub("", " ".join(str(value).split())).strip()


def _type(raw: str) -> str:
    """Render a type for a Markdown table cell.

    ``X | None`` becomes ``X?``: the pipe would otherwise be read as a column
    separator and split the row.

    Args:
        raw: Type as the inventory renders it.

    Returns:
        A table-safe type.
    """
    collapsed = re.sub(r"\s*\|\s*None\b", "?", raw)
    return collapsed.replace("|", r"\|")


def _fmt_ms(value: float | None) -> str:
    """Format a millisecond figure, or ``?`` when unmeasured."""
    return "?" if value is None else f"{value:.0f}ms"


def _fmt_pct(rate: float | None) -> str:
    """Format a fill rate as a percentage, or ``-`` when unmeasured."""
    return "-" if rate is None else f"{rate * 100:.0f}%"


def _coverage_index(endpoint: Endpoint) -> dict[str, bool]:
    """Map each filter param and ``sort=field`` key to whether an index covers it.

    Args:
        endpoint: The endpoint to read coverage from.

    Returns:
        Mapping of coverage key to covered flag.
    """
    return {c["param"]: bool(c["covered"]) for c in endpoint.coverage if c.get("param")}


def _representative(endpoint: Endpoint) -> Timing | None:
    """Return the timing that best describes an ordinary call.

    The slowest probe is usually a deliberate worst case (max page, unindexed
    sort). The typical first-screen call is the median probe, so that is what a
    designer is shown; worst cases surface in the Costly list instead.

    Args:
        endpoint: The endpoint to summarise.

    Returns:
        A timing, or ``None`` if the endpoint was never probed.
    """
    timings = endpoint.timings
    if not timings:
        return None
    return timings[len(timings) // 2]


def _filters_line(endpoint: Endpoint) -> str | None:
    """Render the filter list, marking anything no index covers.

    Args:
        endpoint: The endpoint to describe.

    Returns:
        A Markdown line, or ``None`` when the endpoint takes no filters.
    """
    covered = _coverage_index(endpoint)
    parts: list[str] = []
    for param in endpoint.params:
        if param.location != "query" or param.name in PAGING_PARAMS:
            continue
        mark = "" if covered.get(param.name, True) else " ✗"
        parts.append(f"`{param.name}`{mark}")
    if not parts:
        return None
    # On a write these are switches that change what the call does, not filters.
    label = "Query" if endpoint.is_write else "Filters"
    return f"{label}: " + " · ".join(parts)


def _sort_line(endpoint: Endpoint) -> str | None:
    """Render the sort list, marking anything no index covers.

    Args:
        endpoint: The endpoint to describe.

    Returns:
        A Markdown line, or ``None`` when the endpoint has no sort control.
    """
    pagination = endpoint.pagination
    fields = pagination.get("sort_fields") or []
    if not fields:
        return None
    covered = _coverage_index(endpoint)
    parts = []
    for name in fields:
        mark = "" if covered.get(f"sort={name}", True) else " ✗"
        parts.append(f"`{name}`{mark}")
    default = pagination.get("default_sort")
    suffix = f" (default `{default}`)" if default else ""
    return "Sort: " + " · ".join(parts) + suffix


def _page_line(endpoint: Endpoint) -> str | None:
    """Render page size, cap and cursor style.

    Args:
        endpoint: The endpoint to describe.

    Returns:
        A Markdown line, or ``None`` when the endpoint is not a collection.
    """
    pagination = endpoint.pagination
    style = pagination.get("style")
    if style == "keyset":
        return (
            f"Page: {pagination['default_limit']} default, "
            f"{pagination['max_limit']} max. Cursor `after`/`before`; no total count."
        )
    if style == "none" and endpoint.method == "GET" and endpoint.row_model:
        return "Page: none. Returns the whole collection in one response."
    return None


def _returns_line(endpoint: Endpoint) -> str:
    """Describe what an endpoint returns.

    Args:
        endpoint: The endpoint to describe.

    Returns:
        A Markdown line.
    """
    row = endpoint.row_model
    model = endpoint.response_model
    if row and model and model.startswith("Paginated"):
        return f"Returns: page of `{row}`."
    if row:
        return f"Returns: `{row}` rows."
    if model:
        return f"Returns: `{model}`."
    return "Returns: no body."


def _fields_table(endpoint: Endpoint, inventory: Inventory, table: str | None) -> list[str]:
    """Render the response fields with fill rates.

    Args:
        endpoint: The endpoint whose response is described.
        inventory: Source of fill rates.
        table: Table backing the row model, or ``None`` if none does.

    Returns:
        Markdown lines for the field table.
    """
    fields = endpoint.fields
    if not fields:
        return []
    total = inventory.row_counts.get(table or "", 0)
    heading = f"Fields — `{endpoint.row_model or endpoint.response_model}`"
    if table:
        heading += f" (fill rate over {total:,} rows)"
    lines = [heading, "", "| field | type | filled |", "|---|---|---|"]
    for f in fields:
        rate = _fmt_pct(inventory.fill_rate(table, f.name))
        note = f" *needs `{f.conditional_on}`*" if f.conditional_on else ""
        lines.append(f"| `{f.name}`{note} | {_type(f.type_)} | {rate} |")
    lines.append("")
    return lines


def _write_block(endpoint: Endpoint) -> list[str]:
    """Render the write contract: required fields, omission, errors.

    Args:
        endpoint: A write endpoint.

    Returns:
        Markdown lines, empty for a read endpoint.
    """
    contract = endpoint.write_contract
    if not contract:
        return []
    lines: list[str] = []

    body_fields = endpoint.request_body_fields
    required = [f["name"] for f in body_fields if f.get("required")]
    optional = [f["name"] for f in body_fields if not f.get("required")]
    if required:
        lines.append("Required: " + " · ".join(f"`{n}`" for n in required))
    if optional:
        lines.append("Optional: " + " · ".join(f"`{n}`" for n in optional))
    if not body_fields:
        lines.append("No request body.")

    omission = contract.get("omission_semantics")
    if omission:
        lines.append(f"Omitted field: {_prose(omission)}")

    if contract.get("atomic") is False:
        note = contract.get("atomicity_note") or "not atomic"
        lines.append(f"**Partial failure:** {_prose(note)}")

    delete = contract.get("delete") or {}
    if delete.get("destroys"):
        lines.append(
            f"Destroys {delete['destroys']}; detaches {delete.get('detaches', 'nothing')}. "
            f"UI wording: {delete.get('ui_vocabulary', 'Delete')}."
        )

    lines.extend(_error_lines(contract.get("errors") or []))
    return lines


def _error_lines(errors: list[dict[str, object]]) -> list[str]:
    """Render error codes, spelling out only what is specific to this endpoint.

    The five boilerplate conditions are stated once in the legend. Anything an
    endpoint adds beyond them — a containment cycle, an upload rejection — is
    written out, because those are the states a designer has to draw.

    Args:
        errors: Error records from a write contract.

    Returns:
        Markdown lines, empty when there are no errors.
    """
    if not errors:
        return []
    codes: list[str] = []
    specific: list[str] = []
    for error in errors:
        code = str(error["status"])
        if code not in codes:
            codes.append(code)
        condition = str(error["condition"])
        if condition not in GENERIC_CONDITIONS:
            specific.append(f"`{code}` {_prose(condition)}")
    lines = ["Errors: " + " · ".join(f"`{c}`" for c in sorted(codes))]
    lines.extend(f"  - {s}" for s in specific)
    return lines


def _endpoint_block(
    endpoint: Endpoint,
    inventory: Inventory,
    rendered_models: set[str],
    models: dict[str, str | None],
) -> list[str]:
    """Render one endpoint in full.

    Args:
        endpoint: The endpoint to render.
        inventory: Source of fill rates and row counts.
        rendered_models: Models already given a field table; mutated here so a
            model is described once per document.
        models: Row model to table mapping.

    Returns:
        Markdown lines.
    """
    lines = [f"#### {endpoint.route}", ""]
    for line in (
        _returns_line(endpoint),
        _page_line(endpoint),
        _sort_line(endpoint),
        _filters_line(endpoint),
    ):
        if line:
            lines.append(line)

    timing = _representative(endpoint)
    if timing:
        count = timing.item_count
        rows = f", {count} row{'' if count == 1 else 's'}" if count else ""
        lines.append(
            f"Measured: p50 {_fmt_ms(timing.p50_ms)} / p95 {_fmt_ms(timing.p95_ms)}"
            f" (n={timing.runs}{rows})"
        )

    lines.extend(_write_block(endpoint))
    lines.append("")

    model = endpoint.row_model or endpoint.response_model
    if model and model not in rendered_models and not endpoint.is_write:
        rendered_models.add(model)
        lines.extend(_fields_table(endpoint, inventory, models.get(model)))
    elif model in rendered_models:
        lines.append(f"Fields as `{model}` above.")
        lines.append("")
    return lines


def _also_line(endpoint: Endpoint) -> str:
    """Render a one-line entry for a supporting endpoint.

    Args:
        endpoint: The endpoint to describe.

    Returns:
        A Markdown list item.
    """
    detail = _returns_line(endpoint).removeprefix("Returns: ").rstrip(".")
    return f"- `{endpoint.route}` — {detail}"


def _supporting_ceiling(inventory: Inventory, surface_map: SurfaceMap) -> float:
    """Return the slowest measured p95 among supporting endpoints.

    Supporting endpoints carry no individual timing, so the document states one
    bound covering all of them. Computing it here keeps that claim true: if one
    of them slows down, the stated ceiling rises with it.

    Args:
        inventory: Loaded inventory.
        surface_map: The validated surface map.

    Returns:
        The highest representative p95 in milliseconds, or 0.0 if none measured.
    """
    worst = 0.0
    for surface in surface_map.surfaces:
        for operation in surface.also:
            if surface_map.primary_owner(operation):
                continue
            endpoint = inventory.endpoints[operation]
            # An endpoint the Costly list already names is excluded, or it would
            # set a ceiling that the very next section contradicts.
            if any((t.p95_ms or 0) >= SLOW_MS for t in endpoint.timings):
                continue
            timing = _representative(endpoint)
            if timing and timing.p95_ms:
                worst = max(worst, timing.p95_ms)
    return worst


def _surface_section(
    surface: Surface,
    inventory: Inventory,
    surface_map: SurfaceMap,
    rendered_models: set[str],
) -> list[str]:
    """Render one design surface.

    Args:
        surface: The surface to render.
        inventory: Loaded inventory.
        surface_map: The validated surface map.
        rendered_models: Models already given a field table.

    Returns:
        Markdown lines.
    """
    titles = {s.key: s.title for s in surface_map.surfaces}
    lines = [f"## {surface.title}", "", surface.summary, ""]

    own: list[str] = []
    elsewhere: dict[str, list[str]] = {}
    for operation in surface.operations:
        owner = surface_map.primary_owner(operation)
        if owner is not None and owner != surface.key:
            elsewhere.setdefault(owner, []).append(operation)
        elif operation in surface.primary:
            lines.extend(
                _endpoint_block(
                    inventory.endpoints[operation],
                    inventory,
                    rendered_models,
                    surface_map.models,
                )
            )
        else:
            own.append(operation)

    # One line per borrowed surface rather than one per borrowed endpoint: the
    # Curated surface is almost entirely Organise's containment routes.
    for owner, operations in elsewhere.items():
        routes = ", ".join(f"`{inventory.endpoints[op].route}`" for op in operations)
        lines.extend([f"Used here, specified under **{titles.get(owner, owner)}**: {routes}", ""])

    if own:
        lines.extend(["Also on this surface:", ""])
        lines.extend(_also_line(inventory.endpoints[op]) for op in own)
        lines.append("")
    return lines


def _costly(inventory: Inventory, surface_map: SurfaceMap) -> list[str]:
    """Build the Costly list.

    Anything a surface could reasonably call that is slow or returns an
    uncapped collection, with the measured number.

    Args:
        inventory: Loaded inventory.
        surface_map: The validated surface map.

    Returns:
        Markdown lines.
    """
    on_surface = {op for surface in surface_map.surfaces for op in surface.operations}
    lines: list[str] = []
    unmeasured: list[str] = []
    for operation in sorted(on_surface):
        endpoint = inventory.endpoints[operation]
        for timing in endpoint.timings:
            if (timing.p95_ms or 0) >= SLOW_MS:
                rows = f", {timing.item_count} rows" if timing.item_count else ""
                lines.append(
                    f"- `{endpoint.route}` — p95 {_fmt_ms(timing.p95_ms)}"
                    f" ({timing.probe.replace('-', ' ')}{rows})."
                )
                break

    for operation in sorted(on_surface):
        endpoint = inventory.endpoints[operation]
        if (
            endpoint.pagination.get("style") != "none"
            or endpoint.method != "GET"
            or not endpoint.row_model
        ):
            continue
        scoped = "{" in endpoint.path
        override = surface_map.fan_out.get(operation)
        parent: str | None
        child: str | None
        if override:
            parent, child = override[0], override[1]
        else:
            child = surface_map.models.get(endpoint.row_model)
            segments = [s for s in endpoint.path.split("/") if s and s != "api"]
            parent = surface_map.path_roots.get(segments[0]) if segments else None
        fan_out = inventory.fan_out(parent, child) if scoped else None

        if fan_out is not None:
            worst = int(fan_out["max_children"] or 0)
            if worst < UNCAPPED_ROWS:
                continue
            lines.append(
                f"- `{endpoint.route}` — no cap. Up to {worst:,} rows for one "
                f"parent (median {fan_out['median_children']:.0f})."
            )
            continue

        if scoped:
            unmeasured.append(endpoint.route)
            continue

        total = inventory.row_counts.get(child or "", 0)
        if total >= UNCAPPED_ROWS:
            lines.append(f"- `{endpoint.route}` — no cap. Returns all {total:,} rows.")

    if unmeasured:
        routes = ", ".join(f"`{r}`" for r in unmeasured)
        lines.append(
            f"- Uncapped, and the size per parent has not been measured: {routes}. "
            "Assume any of them can return a long list."
        )
    return lines


def render(inventory: Inventory, surface_map: SurfaceMap) -> str:
    """Render the whole contract.

    Args:
        inventory: Loaded capability inventory.
        surface_map: Validated surface map.

    Returns:
        The Markdown document.
    """
    counts = inventory.row_counts
    lines: list[str] = [
        "# Front-end API contract",
        "",
        f"media-api {inventory.app_version}. Base path `/api`. Every request "
        "carries a bearer token; there are no roles and no per-object permissions.",
        "",
        f"Measured against the live library: {counts.get('titles', 0):,} titles, "
        f"{counts.get('assets', 0):,} assets, {counts.get('title_contents', 0):,} "
        "containment edges.",
        "",
        "## Reading this document",
        "",
        "- **✗** marks a filter or sort with no index behind it. Those controls "
        "scan the whole table; do not offer them prominently.",
        "- **filled** is the share of rows where a field has a value. A field at "
        "56% needs a designed empty state; one at 100% does not. `-` means the "
        "field is computed or nested, so there is no measured rate.",
        "- `?` on a type means the field can be null.",
        "- Timings are observed, not modelled. At n=7 the p95 is effectively the "
        "slowest run seen. Endpoints listed under *Also on this surface* are "
        f"all at or under p95 {_fmt_ms(_supporting_ceiling(inventory, surface_map))}"
        " except where **Costly** says otherwise.",
        "- Listings return a cursor, never a total. There is no page count.",
        "- Every write is last-write-wins: no ETag, no version field, no conflict " "detection.",
        "- Every write can return `401` (token rejected), `404` (no such row), "
        "`409` (constraint or illegal relationship), `422` (validation) and "
        "`423` (database read-only). Endpoint entries spell out only the "
        "conditions beyond those.",
        "",
    ]

    if surface_map.resolved:
        lines.extend(
            [
                "## Changed since the brief",
                "",
                "Section 6 and 7 of the brief list these as missing. They exist now.",
                "",
            ]
        )
        for item in surface_map.resolved:
            lines.append(f"- **{item.brief}** — {item.now}")
        lines.append("")

    rendered_models: set[str] = set()
    for surface in surface_map.surfaces:
        lines.extend(_surface_section(surface, inventory, surface_map, rendered_models))

    lines.extend(
        ["## Do not call", "", "Worker and machine routes. Nothing is designed against these.", ""]
    )
    for note in surface_map.do_not_call:
        endpoint = inventory.endpoints[note.operation]
        lines.append(f"- `{endpoint.route}` — {note.reason}")
    lines.append("")

    lines.extend(
        [
            "## Not available",
            "",
            "Constraints on the design, not a roadmap. Do not draw these.",
            "",
        ]
    )
    for gap in surface_map.not_available:
        lines.append(f"- **{gap.capability}** ({gap.issue}) — {gap.detail}")
    lines.append("")

    costly = _costly(inventory, surface_map)
    lines.extend(
        [
            "## Costly",
            "",
            "Reachable from a surface and expensive. Nothing here should be "
            "driven from a keystroke, and nothing uncapped should be rendered "
            "without virtualising it.",
            "",
        ]
    )
    lines.extend(costly)
    lines.append("")

    return "\n".join(lines)
