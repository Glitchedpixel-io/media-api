"""Render the inventory to Markdown and to JSON.

Both outputs are deterministic: collections are sorted by a stable key, floats
are rounded at fixed precision, and nothing but a timing carries run-to-run
variation. Two runs against unchanged inputs therefore diff to nothing, which is
the whole point of committing the JSON alongside the Markdown.

The Markdown is written for two readers at once. A designer reads the summary
table and the **UI verdict** lines; an LLM asked "can I build X on this API"
reads the per-endpoint sections, where every claim is adjacent to the
measurement that produced it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from .models import (
    DataShape,
    EndpointRecord,
    Inventory,
    ProbeResult,
    RouteSurface,
)

_VERDICT_ICON = {
    "safe": "safe",
    "caution": "caution",
    "unsafe": "not safe",
    "write": "write",
    "unknown": "UNKNOWN",
}


def _escape(text: str | None) -> str:
    """Make a value safe to place inside a Markdown table cell."""
    if text is None:
        return "—"
    return str(text).replace("|", "\\|").replace("\n", " ")


def _bytes(value: int | None) -> str:
    """Human-readable byte count."""
    if value is None:
        return "UNKNOWN"
    if value < 1024:
        return f"{value}B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value / (1024 * 1024):.2f}MB"


def _timing(probe: ProbeResult) -> str:
    """Render a probe's timing, or why there isn't one."""
    if probe.status != "ok" or probe.timing is None:
        return f"{probe.status.upper()} — {probe.reason or 'no reason recorded'}"
    timing = probe.timing
    parts = [f"p50 {timing.p50_ms:.0f}ms", f"p95 {timing.p95_ms:.0f}ms"]
    if timing.ttfb_p50_ms is not None:
        parts.append(f"TTFB p50 {timing.ttfb_p50_ms:.0f}ms / p95 {timing.ttfb_p95_ms:.0f}ms")
    parts.append(_bytes(probe.bytes_))
    if probe.item_count is not None:
        parts.append(f"{probe.item_count} items")
    parts.append(f"n={timing.runs}")
    return " · ".join(parts)


def _pagination_line(record: EndpointRecord) -> str:
    """One line describing how the endpoint pages."""
    if record.annotation is None:
        return "UNKNOWN"
    pagination = record.annotation.pagination
    if pagination.style == "none":
        return f"none — {pagination.stability_note}"
    cap = f"cap {pagination.max_limit}" if pagination.max_limit else "**no cap**"
    default = f"default {pagination.default_limit}" if pagination.default_limit else ""
    stability = {
        True: "ordering stable under concurrent writes",
        False: "**ordering NOT stable under concurrent writes**",
        None: "ordering stability UNKNOWN",
    }[pagination.stable_under_writes]
    bits = [pagination.style, default, cap, stability]
    line = ", ".join(b for b in bits if b)
    if pagination.stability_note:
        line += f" ({pagination.stability_note})"
    if pagination.deep_page_ceiling:
        line += f". Ceiling: {pagination.deep_page_ceiling}"
    return line


def _data_shape_line(record: EndpointRecord, shape: DataShape | None) -> str:
    """Row counts and fill rates for the fields this endpoint returns."""
    if shape is None:
        return "UNKNOWN — Phase 3 did not run (`--skip-db`, or no `CAPINV_DATABASE_URL`)."
    annotation = record.annotation
    if annotation is None:
        return "UNKNOWN — the handler could not be resolved, so its tables are unknown."

    tables = sorted({t for query in annotation.queries for t in query.tables})
    if not tables:
        return "no database tables are read by this endpoint."

    success = next(
        (r for r in record.surface.responses if r.status == record.surface.success_status),
        None,
    )
    field_names = {f.name for f in success.fields} if success else set()

    parts: list[str] = []
    for table in tables:
        count = shape.row_counts.get(table)
        parts.append(f"`{table}` {count:,} rows" if count is not None else f"`{table}` UNKNOWN")

    interesting = [
        column
        for column in shape.columns
        if column.table in tables
        and column.column in field_names
        and column.fill_rate < 1.0
        and column.total > 0
    ]
    for column in sorted(interesting, key=lambda c: c.fill_rate)[:8]:
        parts.append(f"`{column.column}` {column.fill_rate * 100:.0f}% filled")

    conditional = [f.name for f in (success.fields if success else ()) if f.conditional_on]
    if conditional:
        parts.append(
            "fill rate not reported for "
            + ", ".join(f"`{c}`" for c in sorted(conditional))
            + " (returned empty unless `include=` asks for them)"
        )
    return " · ".join(parts)


def _facets(record: EndpointRecord, shape: DataShape | None) -> list[str]:
    """Columns from this endpoint's tables that could become UI facets."""
    if shape is None or record.annotation is None:
        return []
    tables = {t for query in record.annotation.queries for t in query.tables}
    out: list[str] = []
    for column in shape.columns:
        if column.table not in tables or not column.facet_candidate:
            continue
        top = ", ".join(f"{value} ({count:,})" for value, count in column.top_values[:5])
        out.append(
            f"`{column.table}.{column.column}` — {column.distinct} distinct"
            + (
                f"; most common: {top}"
                if top
                else "; values withheld (see `--include-example-values`)"
            )
        )
    return out


def _collections_for(record: EndpointRecord, shape: DataShape | None) -> list[str]:
    """Collection-size distributions relevant to this endpoint."""
    if shape is None or record.annotation is None:
        return []
    tables = {t for query in record.annotation.queries for t in query.tables}
    out: list[str] = []
    for collection in shape.collections:
        if collection.child_table not in tables:
            continue
        out.append(
            f"`{collection.child_table}` per `{collection.parent_table}` — "
            f"min {collection.min_children} / median {collection.median_children:.0f} / "
            f"p95 {collection.p95_children:.0f} / max {collection.max_children:,} "
            f"({collection.parents_with_children:,} of "
            f"{collection.parents_total:,} parents have any)"
        )
    return out


def _param_table(record: EndpointRecord) -> list[str]:
    """The parameter cost table."""
    surface = record.surface
    annotation = record.annotation
    coverage = {c.param: c for c in annotation.coverage} if annotation else {}

    rows: list[str] = [
        "| Param | In | Type | Indexed | Cost note |",
        "|-------|----|------|---------|-----------|",
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

    for entry in (annotation.coverage if annotation else ()):
        if entry.role not in {"sort", "lookup"}:
            continue
        if entry.covered is True:
            indexed = f"yes (`{entry.index}`)" if entry.index else "yes"
        elif entry.covered is False:
            indexed = "**no**"
        else:
            indexed = "UNKNOWN"
        label = entry.param if entry.role == "lookup" else entry.param
        rows.append(
            f"| `{_escape(label)}` | {entry.role} | — | {indexed} | {_escape(entry.note)} |"
        )

    if len(rows) == 2:
        return []
    return rows


def _query_lines(record: EndpointRecord) -> list[str]:
    """One line per database statement the request issues."""
    if record.annotation is None:
        return []
    out: list[str] = []
    for query in record.annotation.queries:
        tables = ", ".join(f"`{t}`" for t in query.tables) or "unresolved table"
        flag = " **[N+1]**" if query.in_loop else ""
        where = f"{query.source_file}:{query.line}" if query.line else query.source_file
        detail = f" — {query.loop_note}" if query.in_loop and query.loop_note else ""
        out.append(f"- `{query.kind}` on {tables} in `{query.owner}` ({where}){flag}{detail}")
    return out


def _response_fields(surface: RouteSurface) -> list[str]:
    """Describe the success response's shape."""
    success = next((r for r in surface.responses if r.status == surface.success_status), None)
    if success is None:
        return []
    lines = [
        f"**Response {success.status}:** `{success.model or 'no model declared'}`"
        + (f" (rows are `{success.row_model}`)" if success.row_model else "")
    ]
    if success.fields:
        rendered = []
        for field in success.fields:
            mark = "?" if field.nullable else ""
            suffix = f" *(only with `{field.conditional_on}`)*" if field.conditional_on else ""
            rendered.append(f"`{field.name}{mark}: {field.type_}`{suffix}")
        lines.append("Fields: " + ", ".join(rendered))
    others = [r for r in surface.responses if r.status != surface.success_status]
    if others:
        lines.append(
            "Also declares: "
            + ", ".join(f"`{r.status}` {r.description or ''}".strip() for r in others)
        )
    return lines


def _endpoint_section(record: EndpointRecord, shape: DataShape | None) -> str:
    """Render one endpoint's section."""
    surface = record.surface
    lines: list[str] = [f"### {surface.method} {surface.path}", ""]

    purpose = surface.summary or (surface.operation_id or "").replace("_", " ") or "—"
    lines.append(f"**Purpose:** {purpose}")
    lines.append(f"**Auth:** {surface.auth}")
    lines.append(f"**Handler:** `{surface.handler_module}.{surface.handler_name}`")
    lines.append(f"**Pagination:** {_pagination_line(record)}")
    if surface.is_streaming:
        lines.append("**Streaming:** yes — the body is produced incrementally, not serialised")
    if surface.trailing_slash_required:
        lines.append(
            "**Trailing slash:** required — the same path without it returns a 307 redirect"
        )
    lines.append("")

    lines.extend(_response_fields(surface))
    lines.append("")

    table = _param_table(record)
    if table:
        lines.extend(table)
        lines.append("")

    queries = _query_lines(record)
    if queries:
        lines.append("**Queries issued:**")
        lines.extend(queries)
        lines.append("")

    annotation = record.annotation
    if annotation:
        extras: list[str] = []
        if annotation.external_calls:
            extras.append(
                "**External calls:** " + ", ".join(f"`{c}`" for c in annotation.external_calls)
            )
        if annotation.filesystem_access:
            extras.append(
                "**Filesystem:** " + ", ".join(f"`{c}`" for c in annotation.filesystem_access)
            )
        if annotation.hard_limits:
            extras.append("**Limits:** " + "; ".join(annotation.hard_limits))
        if extras:
            lines.extend(extras)
            lines.append("")

    lines.append(f"**Data shape:** {_data_shape_line(record, shape)}")

    collections = _collections_for(record, shape)
    if collections:
        lines.append("**Collection sizes:** " + " · ".join(collections))

    facets = _facets(record, shape)
    if facets:
        lines.append("**Facet candidates:** " + " · ".join(facets))

    if record.probes:
        lines.append("**Measured:**")
        for probe in record.probes:
            note = f" — {probe.notes[0]}" if probe.notes else ""
            lines.append(f"- `{probe.name}` (`{probe.url}`): {_timing(probe)}{note}")
            for extra in probe.notes[1:]:
                lines.append(f"  - {extra}")
    else:
        lines.append(
            "**Measured:** UNKNOWN — no probe covers this endpoint "
            "(add one to `probes.yaml`, or Phase 4 was skipped)."
        )

    if record.risks:
        lines.append("**Risk:**")
        for risk in record.risks:
            lines.append(f"- {risk}")
    else:
        lines.append("**Risk:** none identified by this run.")

    lines.append(f"**UI verdict:** {record.verdict}")
    lines.append("")
    return "\n".join(lines)


def _first_sentence(text: str, limit: int = 150) -> str:
    """The first sentence of a verdict, for the summary table.

    Kept whole rather than stripped down to a fragment: "UNKNOWN — no timings
    were taken" is the useful part, and splitting on the dash throws it away.
    """
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
        p95 = f"{max(p.timing.p95_ms for p in timed if p.timing):.0f}ms" if timed else "UNKNOWN"
        auth = "**none**" if surface.auth.startswith("none") else "bearer"
        rows.append(
            f"| `{surface.method} {surface.path}` | {auth} | {paging} | {p95} | "
            f"{_VERDICT_ICON.get(record.verdict_class, record.verdict_class)} | "
            f"{_escape(_first_sentence(record.verdict))} |"
        )
    return rows


def _candidates_for_removal(records: tuple[EndpointRecord, ...]) -> list[str]:
    """The Candidates for removal section."""
    lines = ["## Candidates for removal", ""]
    unreferenced = [r for r in records if r.usage and not r.usage.referenced]
    if not unreferenced:
        lines.append("Every endpoint has at least one reference from the evidence available.")
        lines.append("")
        return lines

    strength = {r.usage.strength for r in unreferenced if r.usage}
    if strength == {"weak"}:
        lines.append(
            "> **Evidence is weak.** No consumer codebase or access log was supplied, "
            "so this list is derived from references inside this repository alone. "
            "Several endpoints here exist for machine consumers that live elsewhere — "
            "the transform-request claim and heartbeat routes are a worker pull queue, "
            "and no front end would ever call them. Treat this as a list of things to "
            "*ask about*, not a list of things to delete. Re-run with "
            "`--frontend-path` or `--access-log` for evidence that can actually "
            "support a removal."
        )
        lines.append("")

    lines.append("| Endpoint | Evidence | Tests referencing it |")
    lines.append("|---|---|---|")
    for record in unreferenced:
        usage = record.usage
        if usage is None:
            continue
        tests = ", ".join(f"`{t}`" for t in usage.test_references) or "none"
        lines.append(
            f"| `{record.surface.method} {record.surface.path}` | "
            f"{usage.strength}: no reference found | {_escape(tests)} |"
        )
    lines.append("")
    lines.append("Nothing has been deleted. This is a list of questions, not actions.")
    lines.append("")
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
        lines.append("No `UNKNOWN` values were produced by this run.")
        lines.append("")
        return lines

    lines.append(
        f"{len(collected)} value(s) could not be established. Each is listed with the "
        "specific thing that would settle it."
    )
    lines.append("")
    lines.append("| Scope | Not known | What would settle it |")
    lines.append("|---|---|---|")
    for unknown in sorted(collected, key=lambda u: (u.scope, u.question)):
        lines.append(
            f"| `{_escape(unknown.scope)}` | {_escape(unknown.question)} | "
            f"{_escape(unknown.resolution)} |"
        )
    lines.append("")
    return lines


def to_markdown(inventory: Inventory) -> str:
    """Render the whole report."""
    records = inventory.endpoints
    lines: list[str] = [
        "# Capability inventory",
        "",
        "Generated by `uv run capability-inventory`. Do not hand-edit — re-run the "
        "harness instead. The machine-readable form of this document is "
        "`docs/capability-inventory.json`.",
        "",
        "This document answers one question per endpoint: **what can a front end "
        "responsibly build on it?** Everything above each `UI verdict` line is the "
        "evidence for it. Where a value is not known, it says `UNKNOWN` and the "
        "[Gaps](#gaps) section says what would settle it.",
        "",
        "## Run",
        "",
        f"- **Source:** {inventory.generated_from}",
        f"- **App version:** {inventory.app_version}",
        f"- **Phases run:** {', '.join(inventory.phases_run) or 'none'}",
        f"- **Phases skipped:** {', '.join(inventory.phases_skipped) or 'none'}",
        f"- **Endpoints:** {len(records)}",
    ]
    if inventory.data_shape:
        lines.append(f"- **Database:** {inventory.data_shape.captured_from}")
        lines.append(f"- **Server:** {inventory.data_shape.server_version}")
        rtt = inventory.data_shape.baseline_rtt_ms
        if rtt is not None:
            lines.append(
                f"- **Baseline database round trip:** {rtt:.0f}ms median for "
                "`SELECT 1`. This is the unit cost of a query issued once per row, "
                "and it is a property of where the harness ran relative to the "
                "database rather than of the API. An API co-located with its "
                "database would see a far smaller number for the same defect — so "
                "read a per-row cost below as evidence of *how many* queries an "
                "endpoint issues, and treat the absolute milliseconds as specific to "
                "this measurement setup."
            )
    for note in inventory.notes:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.extend(_summary_table(records))
    lines.append("")

    lines.append("## Endpoints")
    lines.append("")
    for record in records:
        lines.append(_endpoint_section(record, inventory.data_shape))

    lines.extend(_candidates_for_removal(records))
    lines.extend(_gaps(inventory))

    lines.append("## Index inventory")
    lines.append("")
    lines.append(
        "Every index the coverage judgements above were made against, merged from "
        "the SQLAlchemy metadata and the Alembic migrations."
    )
    lines.append("")
    lines.append("| Table | Index | Columns | Unique | Source |")
    lines.append("|---|---|---|---|---|")
    for index in inventory.indexes:
        columns = ", ".join(f"`{c}`" for c in index.columns) or f"`{index.expression}`"
        where = f" WHERE {index.where}" if index.where else ""
        lines.append(
            f"| `{index.table}` | `{index.name}` | {_escape(columns + where)} | "
            f"{'yes' if index.unique else 'no'} | {index.source} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


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

    Keys are sorted and floats rounded so successive runs diff cleanly in git.
    Only the ``probes`` timings should ever change between two runs against
    unchanged inputs.
    """
    payload = _plain(inventory)
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
