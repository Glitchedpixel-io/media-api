"""Render the inventory to Markdown and to JSON.

Both outputs are deterministic: collections are sorted by a stable key, floats
are rounded at fixed precision, and nothing but a timing carries run-to-run
variation. Two runs against unchanged inputs therefore diff to nothing, which is
the whole point of committing the JSON alongside the Markdown.

The Markdown is written for two readers at once. A designer reads the summary
table and the verdict blockquotes; an LLM asked "can I build X on this API"
reads the per-endpoint sections, where every claim sits next to the measurement
that produced it.

Three structural rules earn their keep, and ``tests/unit/tools`` enforces all
three against the rendered output:

* **The verdict opens the section, not closes it.** At ninety-odd endpoints
  nobody reads bottom-up, and the conclusion is the reason the document exists.
  It is a blockquote beginning with a bare severity token so it survives being
  skimmed, and so the severities can be counted with ``grep``.
* **Header facts are a table.** Consecutive ``**Label:** value`` lines are lazy
  continuation lines of a single CommonMark paragraph, so a renderer collapses
  them into one run-on block. A table cannot collapse.
* **One table's facts are written once.** Row counts and fill rates belong to
  database tables, not endpoints; twenty-seven endpoints read ``assets`` and
  none of them needs to restate how many rows it has. Endpoints link to the
  appendix instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any

from .models import (
    ColumnStats,
    DataShape,
    EndpointRecord,
    Inventory,
    ProbeResult,
    RouteSurface,
)

# Severity shown in the summary table's Verdict column.
_VERDICT_LABEL = {
    "safe": "safe",
    "caution": "caution",
    "unsafe": "not safe",
    "write": "write",
    "unknown": "UNKNOWN",
}

# The bare token that opens a section's verdict blockquote. Four values, so the
# set is greppable and a reader learns it once.
SEVERITY_TOKENS = ("SAFE", "CAUTION", "NOT SAFE", "UNKNOWN")

_TOKEN_FOR_CLASS = {
    "safe": "SAFE",
    "caution": "CAUTION",
    "unsafe": "NOT SAFE",
    "unknown": "UNKNOWN",
    # A collapsed single-row write never renders a section. If one reaches here,
    # SAFE is the honest reading of "nothing endpoint-specific to get wrong".
    "write": "SAFE",
}

_ANCHOR_STRIP = re.compile(r"[^0-9a-z _-]")


def _escape(text: str | None) -> str:
    """Make a value safe to place inside a Markdown table cell."""
    if text is None:
        return "—"
    return str(text).replace("|", "\\|").replace("\n", " ")


def _anchor(heading: str) -> str:
    """Slugify a heading the way GitHub does, for intra-document links."""
    slug = _ANCHOR_STRIP.sub("", heading.lower())
    return slug.strip().replace(" ", "-")


def _table_heading(table: str) -> str:
    """The appendix heading for one database table."""
    return f"Table: {table}"


def _table_link(table: str, present: set[str]) -> str:
    """Link a table name to its appendix subsection, if it has one."""
    if table not in present:
        return f"`{table}`"
    return f"[`{table}`](#{_anchor(_table_heading(table))})"


def _bytes(value: int | None) -> str:
    """Human-readable byte count."""
    if value is None:
        return "UNKNOWN"
    if value < 1024:
        return f"{value}B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value / (1024 * 1024):.2f}MB"


def _duration(ms: float) -> str:
    """Render a millisecond figure at a sensible scale."""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms:.0f}ms"


def _timing(probe: ProbeResult) -> str:
    """Render a probe's timing, or why there isn't one."""
    if probe.status != "ok" or probe.timing is None:
        return f"{probe.status.upper()} — {probe.reason or 'no reason recorded'}"
    timing = probe.timing
    parts = [f"p50 {_duration(timing.p50_ms)}", f"p95 {_duration(timing.p95_ms)}"]
    if timing.ttfb_p50_ms is not None and timing.ttfb_p95_ms is not None:
        parts.append(
            f"TTFB p50 {_duration(timing.ttfb_p50_ms)} / p95 {_duration(timing.ttfb_p95_ms)}"
        )
    parts.append(_bytes(probe.bytes_))
    if probe.item_count is not None:
        parts.append(f"{probe.item_count:,} items")
    parts.append(f"n={timing.runs}")
    return " · ".join(parts)


# --------------------------------------------------------------------------
# Section pieces
# --------------------------------------------------------------------------


def _verdict_block(record: EndpointRecord) -> list[str]:
    """The section's opening verdict blockquote.

    Exactly one ``> **<TOKEN>**`` line per section, so the severities can be
    counted from a shell without parsing the document.
    """
    token = _TOKEN_FOR_CLASS.get(record.verdict_class, "UNKNOWN")
    return [f"> **{token}** — {record.verdict.strip()}"]


def _pagination_cell(record: EndpointRecord) -> str:
    """One cell describing how the endpoint pages."""
    if record.annotation is None:
        return "UNKNOWN"
    pagination = record.annotation.pagination
    if pagination.style == "none":
        return f"none — {pagination.stability_note}" if pagination.stability_note else "none"

    parts = [pagination.style]
    if pagination.default_limit:
        parts.append(f"default {pagination.default_limit}")
    parts.append(f"cap {pagination.max_limit}" if pagination.max_limit else "**no cap**")
    parts.append(
        {
            True: "stable under concurrent writes",
            False: "**ordering NOT stable under concurrent writes**",
            None: "ordering stability UNKNOWN",
        }[pagination.stable_under_writes]
    )
    cell = " · ".join(parts)
    if pagination.deep_page_ceiling:
        cell += f" · ceiling: {pagination.deep_page_ceiling}"
    return cell


def _header_table(record: EndpointRecord) -> list[str]:
    """The section's fact table.

    A table rather than a run of ``**Label:** value`` lines, which CommonMark
    treats as one paragraph and renders as a single run-on block.
    """
    surface = record.surface
    rows: list[tuple[str, str]] = []

    purpose = surface.summary or (surface.operation_id or "").replace("_", " ") or "—"
    rows.append(("Purpose", purpose))
    rows.append(("Auth", surface.auth))
    rows.append(("Handler", f"`{surface.handler_module}.{surface.handler_name}`"))
    rows.append(("Pagination", _pagination_cell(record)))

    if surface.is_streaming:
        rows.append(("Streaming", "yes — the body is produced incrementally, not serialised"))
    if surface.trailing_slash_required:
        rows.append(("Trailing slash", "required — 307 redirect without"))

    success = next((r for r in surface.responses if r.status == surface.success_status), None)
    if success is not None:
        model = success.model or "no model declared"
        cell = f"`{model}`"
        if success.row_model and success.row_model != model:
            cell += f", rows are `{success.row_model}`"
        rows.append((f"Response {success.status}", cell))

    errors = [r for r in surface.responses if not r.status.startswith("2")]
    if errors:
        rows.append(
            (
                "Declares",
                ", ".join(f"`{r.status}` {r.description or ''}".strip() for r in errors),
            )
        )

    annotation = record.annotation
    if annotation:
        if annotation.external_calls:
            rows.append(("External calls", ", ".join(f"`{c}`" for c in annotation.external_calls)))
        if annotation.filesystem_access:
            rows.append(("Filesystem", ", ".join(f"`{c}`" for c in annotation.filesystem_access)))
        if annotation.hard_limits:
            rows.append(("Limits", "; ".join(annotation.hard_limits)))

    out = ["| | |", "|---|---|"]
    out.extend(f"| **{label}** | {_escape(value)} |" for label, value in rows)
    return out


def _fields_block(surface: RouteSurface) -> list[str]:
    """The success response's field list."""
    success = next((r for r in surface.responses if r.status == surface.success_status), None)
    if success is None or not success.fields:
        return []
    rendered = []
    for field in success.fields:
        mark = "?" if field.nullable else ""
        suffix = f" *(only with `{field.conditional_on}`)*" if field.conditional_on else ""
        rendered.append(f"`{field.name}{mark}: {field.type_}`{suffix}")
    return ["#### Fields", "", ", ".join(rendered), ""]


def _param_table(record: EndpointRecord) -> list[str]:
    """The parameter cost table."""
    surface = record.surface
    annotation = record.annotation
    coverage = {c.param: c for c in annotation.coverage} if annotation else {}

    rows: list[str] = [
        "| Param | In | Type | Indexed | Cost note |",
        "|---|---|---|---|---|",
    ]
    for param in surface.params:
        entry = coverage.get(param.name)
        if entry is None:
            indexed = "n/a"
            note = param.description or "—"
        elif entry.covered is True:
            indexed = f"yes (`{entry.index}`)" if entry.index else "yes"
            note = entry.note
        elif entry.covered is False:
            indexed = "**no**"
            note = entry.note
        else:
            indexed = "UNKNOWN"
            note = entry.note
        bounds = ", ".join(f"{k}={v}" for k, v in sorted(param.constraints.items()))
        type_ = param.type_ + (f" ({bounds})" if bounds else "")
        rows.append(
            f"| `{_escape(param.name)}` | {param.location} | {_escape(type_)} | "
            f"{indexed} | {_escape(note)} |"
        )

    for entry in annotation.coverage if annotation else ():
        if entry.role not in {"sort", "lookup"}:
            continue
        if entry.covered is True:
            indexed = f"yes (`{entry.index}`)" if entry.index else "yes"
        elif entry.covered is False:
            indexed = "**no**"
        else:
            indexed = "UNKNOWN"
        rows.append(
            f"| `{_escape(entry.param)}` | {entry.role} | — | {indexed} | {_escape(entry.note)} |"
        )

    if len(rows) == 2:
        return []
    return ["#### Parameters", "", *rows, ""]


def _queries_block(record: EndpointRecord) -> list[str]:
    """One bullet per database statement the request issues."""
    if record.annotation is None or not record.annotation.queries:
        return []
    out = ["#### Queries", ""]
    for query in record.annotation.queries:
        tables = ", ".join(f"`{t}`" for t in query.tables) or "unresolved table"
        flag = " **[N+1]**" if query.in_loop else ""
        where = f"{query.source_file}:{query.line}" if query.line else query.source_file
        detail = f" — {query.loop_note}" if query.in_loop and query.loop_note else ""
        out.append(f"- `{query.kind}` on {tables} in `{query.owner}` ({where}){flag}{detail}")
    out.append("")
    return out


def _data_shape_block(
    record: EndpointRecord, shape: DataShape | None, present: set[str]
) -> list[str]:
    """What this endpoint's data looks like, without restating the tables.

    Row counts, fill rates, cardinality and per-parent collection sizes belong
    to a table and live once in the appendix. What belongs here is only what is
    true of *this endpoint*: which tables it reads, which of its response fields
    come back empty unless asked for, and how large its own probe responses
    actually were.
    """
    out = ["#### Data shape", ""]

    annotation = record.annotation
    if annotation is None:
        out += ["UNKNOWN — the handler could not be resolved, so its tables are unknown.", ""]
        return out

    tables = sorted({t for query in annotation.queries for t in query.tables})
    if not tables:
        out += ["This endpoint reads no database tables.", ""]
    elif shape is None:
        out += [
            "Reads "
            + ", ".join(f"`{t}`" for t in tables)
            + ". Row counts and fill rates are UNKNOWN — Phase 3 did not run "
            "(`--skip-db`, or no `CAPINV_DATABASE_URL`).",
            "",
        ]
    else:
        links = " · ".join(_table_link(t, present) for t in tables)
        out += [f"Reads {links} — see the [Tables](#tables) appendix for each.", ""]

    success = next(
        (r for r in record.surface.responses if r.status == record.surface.success_status),
        None,
    )
    conditional = [f for f in (success.fields if success else ()) if f.conditional_on]
    if conditional:
        out += [
            "**Empty unless requested.** These fields serialise as an empty collection "
            "when the caller does not ask for them, which is indistinguishable from "
            "genuinely having none:",
            "",
        ]
        out += [f"- `{f.name}` — populated only with `{f.conditional_on}`" for f in conditional]
        out.append("")

    # The size this endpoint's own responses actually reached. The per-probe
    # detail is in **Measured** directly below; what belongs here is the bound,
    # because that is what a layout has to survive.
    observed = [p for p in record.probes if p.status == "ok" and p.item_count is not None]
    if observed:
        largest = max(observed, key=lambda p: (p.item_count or 0, p.bytes_ or 0))
        smallest = min(observed, key=lambda p: (p.item_count or 0, p.bytes_ or 0))
        if largest.item_count == smallest.item_count:
            span = f"{largest.item_count:,} item(s)"
        else:
            span = f"{smallest.item_count:,} to {largest.item_count:,} items"
        out += [
            f"Own probe responses carried {span}; the largest was "
            f"{_bytes(largest.bytes_)} (`{largest.name}`).",
            "",
        ]
    return out


def _measured_block(record: EndpointRecord) -> list[str]:
    """The probe results for this endpoint."""
    out = ["#### Measured", ""]
    if not record.probes:
        if record.surface.method != "GET":
            # Telling a reader to add a probe here is advice to allowlist a write
            # against whatever instance the run is pointed at -- which for this
            # report is production. The absence is a decision, so it reads as one.
            out += [
                "UNKNOWN — not probed, deliberately. Measuring a mutating endpoint "
                "means issuing the mutation, and the harness refuses any non-GET "
                "unless it is named in `allowlist`, which ships empty. Cost here is "
                "established from the query shape above rather than by timing.",
                "",
            ]
        else:
            out += [
                "UNKNOWN — no probe covers this endpoint. Add one to `probes.yaml`, or "
                "Phase 4 was skipped.",
                "",
            ]
        return out
    for probe in record.probes:
        note = f" — {probe.notes[0]}" if probe.notes else ""
        out.append(f"- `{probe.name}` (`{probe.url}`): {_timing(probe)}{note}")
        for extra in probe.notes[1:]:
            out.append(f"  - {extra}")
    out.append("")
    return out


def _risk_block(record: EndpointRecord) -> list[str]:
    """The risk bullets."""
    out = ["#### Risk", ""]
    if not record.risks:
        out += ["None identified by this run.", ""]
        return out
    out += [f"- {risk}" for risk in record.risks]
    out.append("")
    return out


def _endpoint_section(
    record: EndpointRecord, shape: DataShape | None, present: set[str]
) -> list[str]:
    """Render one endpoint's full section."""
    surface = record.surface
    out = [f"### {surface.method} {surface.path}", ""]
    out += _verdict_block(record)
    out.append("")
    out += _header_table(record)
    out.append("")
    out += _fields_block(surface)
    out += _param_table(record)
    out += _queries_block(record)
    out += _data_shape_block(record, shape, present)
    out += _measured_block(record)
    out += _risk_block(record)
    out += ["---", ""]
    return out


# --------------------------------------------------------------------------
# Whole-document sections
# --------------------------------------------------------------------------


def _first_sentence(text: str, limit: int = 150) -> str:
    """The first sentence of a verdict, for the summary table."""
    head = text.split(". ")[0].rstrip(".")
    if len(head) <= limit:
        return head
    return head[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def _summary_table(records: tuple[EndpointRecord, ...]) -> list[str]:
    """The opening table of every endpoint and its verdict."""
    rows = [
        "| Endpoint | Auth | Paging | Measured p95 | Verdict | One-line judgement |",
        "|---|---|---|---|---|---|",
    ]
    for record in records:
        surface = record.surface
        annotation = record.annotation
        if surface.method != "GET":
            paging = "—"
        elif annotation is None:
            paging = "UNKNOWN"
        elif annotation.pagination.style == "none":
            paging = "**none**"
        else:
            paging = annotation.pagination.style
        timed = [p for p in record.probes if p.status == "ok" and p.timing]
        p95 = _duration(max(p.timing.p95_ms for p in timed if p.timing)) if timed else "UNKNOWN"
        auth = "**none**" if surface.auth.startswith("none") else "bearer"
        rows.append(
            f"| `{surface.method} {surface.path}` | {auth} | {paging} | {p95} | "
            f"{_VERDICT_LABEL.get(record.verdict_class, record.verdict_class)} | "
            f"{_escape(_first_sentence(record.verdict))} |"
        )
    return rows


def _write_table(records: tuple[EndpointRecord, ...]) -> list[str]:
    """Collapse the boilerplate write paths into one table.

    A single-row write with no loops and no filesystem work has nothing
    endpoint-specific for a UI to get wrong beyond ordinary error handling, and
    a section per endpoint saying so three dozen times buries the endpoints that
    do. Only the uniform ones collapse here; a write that loops or touches the
    filesystem keeps a full section, because those have failure modes a design
    has to account for. The columns carry what a client needs in order to call
    them.
    """
    if not records:
        return []
    out = [
        "## Write endpoints",
        "",
        f"{len(records)} mutating endpoints are single-row writes that issue no per-item "
        "queries and touch no filesystem. There is nothing endpoint-specific for a UI to "
        "get wrong beyond ordinary error handling, so they are collapsed here rather than "
        "given a section each. Any write that loops or touches the filesystem keeps a "
        "full section above.",
        "",
        "| Endpoint | Handler | Body | Returns | Errors | Filesystem | Loops |",
        "|---|---|---|---|---|---|---|",
    ]
    for record in records:
        surface = record.surface
        annotation = record.annotation
        success = next((r for r in surface.responses if r.status == surface.success_status), None)
        returns = f"`{success.model}`" if success and success.model else "—"
        errors = (
            ", ".join(f"`{r.status}`" for r in surface.responses if not r.status.startswith("2"))
            or "—"
        )
        body = f"`{surface.request_body}`" if surface.request_body else "—"
        filesystem = "yes" if annotation and annotation.filesystem_access else "no"
        loops = (
            "yes"
            if annotation
            and any(not q.owner.endswith("(ORM lazy load)") for q in annotation.n_plus_one)
            else "no"
        )
        out.append(
            f"| `{surface.method} {surface.path}` | "
            f"`{surface.handler_module.rsplit('.', 1)[-1]}.{surface.handler_name}` | "
            f"{_escape(body)} | {_escape(returns)} | {errors} | {filesystem} | {loops} |"
        )
    out.append("")
    return out


def _table_subsection(table: str, shape: DataShape, columns: list[ColumnStats]) -> list[str]:
    """One appendix subsection for one database table."""
    out = [f"### {_table_heading(table)}", ""]
    count = shape.row_counts.get(table)
    out += [f"**{count:,} rows.**" if count is not None else "Row count UNKNOWN.", ""]

    if columns:
        out += [
            "| Column | Filled | Non-null | Distinct | Facet |",
            "|---|---|---|---|---|",
        ]
        for column in sorted(columns, key=lambda c: (c.fill_rate, c.column)):
            if column.distinct is None:
                distinct = "—"
            elif column.distinct_capped:
                distinct = f"≥{column.distinct:,}"
            else:
                distinct = f"{column.distinct:,}"
            facet = "yes" if column.facet_candidate else "—"
            if column.facet_candidate and column.top_values:
                facet = "yes: " + ", ".join(
                    f"{value} ({n:,})" for value, n in column.top_values[:5]
                )
            out.append(
                f"| `{column.column}` | {column.fill_rate * 100:.0f}% | "
                f"{column.non_null:,} | {distinct} | {_escape(facet)} |"
            )
        out.append("")

    collections = [c for c in shape.collections if c.child_table == table]
    if collections:
        out += [
            "Children per parent:",
            "",
            "| Parent | Via | Min | Median | p95 | Max | Parents with any |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in collections:
            out.append(
                f"| `{c.parent_table}` | `{c.fk_column}` | {c.min_children} | "
                f"{c.median_children:.0f} | {c.p95_children:.0f} | {c.max_children:,} | "
                f"{c.parents_with_children:,} of {c.parents_total:,} |"
            )
        out.append("")
    return out


def _tables_appendix(shape: DataShape | None, present: set[str]) -> list[str]:
    """One subsection per database table, holding everything true of that table."""
    out = ["## Tables", ""]
    if shape is None:
        out += [
            "UNKNOWN — Phase 3 did not run, so no row counts, fill rates, cardinality "
            "or collection sizes were measured. Re-run without `--skip-db`.",
            "",
        ]
        return out

    out += [
        "Row counts, fill rates, cardinality and collection-size distributions are "
        "properties of a table rather than of any endpoint that reads it, so they are "
        "recorded once here and linked from each endpoint's **Data shape**.",
        "",
    ]

    by_table: dict[str, list[ColumnStats]] = {}
    for column in shape.columns:
        by_table.setdefault(column.table, []).append(column)

    for table in sorted(present):
        out += _table_subsection(table, shape, by_table.get(table, []))
    return out


def _candidates_for_removal(records: tuple[EndpointRecord, ...]) -> list[str]:
    """The Candidates for removal section."""
    lines = ["## Candidates for removal", ""]
    unreferenced = [r for r in records if r.usage and not r.usage.referenced]
    if not unreferenced:
        lines += ["Every endpoint has at least one reference from the evidence available.", ""]
        return lines

    strength = {r.usage.strength for r in unreferenced if r.usage}
    if strength == {"weak"}:
        lines += [
            "> **Evidence is weak.** No consumer codebase or access log was supplied, so "
            "this list is derived from references inside this repository alone. Several "
            "endpoints here exist for machine consumers that live elsewhere — the "
            "transform-request claim and heartbeat routes are a worker pull queue, and no "
            "front end would ever call them. Treat this as a list of things to *ask "
            "about*, not a list of things to delete. Re-run with `--frontend-path` or "
            "`--access-log` for evidence that can actually support a removal.",
            "",
        ]

    lines += [
        "| Endpoint | Evidence | Tests referencing it |",
        "|---|---|---|",
    ]
    for record in unreferenced:
        usage = record.usage
        if usage is None:
            continue
        tests = ", ".join(f"`{t}`" for t in usage.test_references) or "none"
        lines.append(
            f"| `{record.surface.method} {record.surface.path}` | "
            f"{usage.strength}: no reference found | {_escape(tests)} |"
        )
    lines += ["", "Nothing has been deleted. This is a list of questions, not actions.", ""]
    return lines


def _gaps(inventory: Inventory) -> list[str]:
    """The Gaps section: every UNKNOWN, with what would settle it."""
    lines = ["## Gaps", ""]
    collected = list(inventory.unknowns)
    for record in inventory.endpoints:
        if record.annotation:
            collected.extend(record.annotation.unknowns)
    if inventory.data_shape:
        collected.extend(inventory.data_shape.unknowns)

    if not collected:
        lines += ["No `UNKNOWN` values were produced by this run.", ""]
        return lines

    lines += [
        f"{len(collected)} value(s) could not be established. Each is listed with the "
        "specific thing that would settle it.",
        "",
        "| Scope | Not known | What would settle it |",
        "|---|---|---|",
    ]
    for unknown in sorted(collected, key=lambda u: (u.scope, u.question)):
        lines.append(
            f"| `{_escape(unknown.scope)}` | {_escape(unknown.question)} | "
            f"{_escape(unknown.resolution)} |"
        )
    lines.append("")
    return lines


def _index_inventory(inventory: Inventory) -> list[str]:
    """The index appendix."""
    lines = [
        "## Index inventory",
        "",
        "Indexes declared by the SQLAlchemy models, which is the schema the running "
        "application has and the only source the coverage judgements above are made "
        "against, followed by every `op.create_index` in the migration history as a "
        "cross-check. Migration rows are **historical, not current**: revision order is "
        "not resolved, so an index created and later dropped or renamed still appears "
        "here. A row sourced from a migration with no matching model row is either drift "
        "or an object that has since been removed — check before acting on it.",
        "",
        "| Table | Index | Columns | Unique | Source |",
        "|---|---|---|---|---|",
    ]
    for index in inventory.indexes:
        columns = ", ".join(f"`{c}`" for c in index.columns) or f"`{index.expression}`"
        where = f" WHERE {index.where}" if index.where else ""
        lines.append(
            f"| `{index.table}` | `{index.name}` | {_escape(columns + where)} | "
            f"{'yes' if index.unique else 'no'} | {index.source} |"
        )
    lines.append("")
    return lines


def _run_block(inventory: Inventory) -> list[str]:
    """The provenance header."""
    lines = [
        "## Run",
        "",
        f"- **Source:** {inventory.generated_from}",
        f"- **App version:** {inventory.app_version}",
        f"- **Phases run:** {', '.join(inventory.phases_run) or 'none'}",
        f"- **Phases skipped:** {', '.join(inventory.phases_skipped) or 'none'}",
        f"- **Endpoints:** {len(inventory.endpoints)}",
    ]
    if inventory.data_shape:
        lines.append(f"- **Database:** {inventory.data_shape.captured_from}")
        lines.append(f"- **Server:** {inventory.data_shape.server_version}")
        rtt = inventory.data_shape.baseline_rtt_ms
        if rtt is not None:
            lines.append(
                f"- **Baseline database round trip:** {rtt:.0f}ms median for `SELECT 1`. "
                "This is the unit cost of a query issued once per row, and it is a "
                "property of where the harness ran relative to the database rather than "
                "of the API. An API co-located with its database would see a far smaller "
                "number for the same defect — so read a per-row cost below as evidence of "
                "*how many* queries an endpoint issues, and treat the absolute "
                "milliseconds as specific to this measurement setup."
            )
    for note in inventory.notes:
        lines.append(f"- {note}")
    lines.append("")
    return lines


def to_markdown(inventory: Inventory) -> str:
    """Render the whole report."""
    records = inventory.endpoints
    shape = inventory.data_shape
    present = set(shape.row_counts) if shape else set()

    collapsed = tuple(r for r in records if r.verdict_class == "write")
    detailed = tuple(r for r in records if r.verdict_class != "write")

    lines: list[str] = [
        "# Capability inventory",
        "",
        "Generated by `uv run capability-inventory`. Do not hand-edit — re-run the "
        "harness instead. The machine-readable form of this document is "
        "`docs/capability-inventory.json`.",
        "",
        "This document answers one question per endpoint: **what can a front end "
        "responsibly build on it?** Each section opens with that answer as a blockquote "
        "tagged `SAFE`, `CAUTION`, `NOT SAFE` or `UNKNOWN`, and everything below it in "
        "the section is the evidence behind it. Where a value could not be established "
        "it says `UNKNOWN`, and the [Gaps](#gaps) section says what would settle it.",
        "",
        "Read the [Summary](#summary) to triage, one section to understand one endpoint, "
        "and the [Tables](#tables) appendix for what the data behind them looks like.",
        "",
    ]
    lines += _run_block(inventory)

    lines += ["## Summary", ""]
    lines += _summary_table(records)
    lines.append("")

    lines += ["## Endpoints", ""]
    if collapsed:
        lines += [
            f"{len(detailed)} endpoints have a section below. The remaining "
            f"{len(collapsed)} are uniform single-row writes, collapsed into "
            "[Write endpoints](#write-endpoints).",
            "",
        ]
    for record in detailed:
        lines += _endpoint_section(record, shape, present)

    lines += _write_table(collapsed)
    lines += _tables_appendix(shape, present)
    lines += _candidates_for_removal(records)
    lines += _gaps(inventory)
    lines += _index_inventory(inventory)

    return _normalise(lines)


def _normalise(lines: list[str]) -> str:
    """Collapse runs of blank lines and guarantee a single trailing newline.

    Each section builder ends with a blank line so the pieces compose without
    the caller tracking spacing. That is the right default, and it occasionally
    produces a double blank -- harmless in Markdown, noisy in a diff.
    """
    out: list[str] = []
    for line in lines:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _plain(value: Any) -> Any:
    """Recursively convert dataclasses to JSON-safe structures."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


def to_json(inventory: Inventory) -> str:
    """Render the machine-readable form.

    Keys are sorted and floats rounded so successive runs diff cleanly. Only the
    ``probes`` timings should ever change between two runs against unchanged
    inputs.
    """
    payload = _plain(inventory)
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
