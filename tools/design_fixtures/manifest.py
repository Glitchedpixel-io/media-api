"""Renders ``manifest.md`` for a fixture run.

One line per fixture: the filename, what it is, how it was selected and how many
records it holds. Cases that matched no data are listed too -- an empty result is a
finding about the library, not a reason to leave a gap in the manifest.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tools.design_fixtures.capture import Finding, Fixture

ONE_MEGABYTE = 1024 * 1024


def human_size(size_bytes: int) -> str:
    """Render a byte count for reading.

    Args:
        size_bytes: The size in bytes.

    Returns:
        str: e.g. ``1.4 MB``, ``812 B``.
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < ONE_MEGABYTE:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / ONE_MEGABYTE:.2f} MB"


def _escape(text: str) -> str:
    """Make a string safe to sit inside a markdown table cell.

    Args:
        text: The raw text.

    Returns:
        str: The text with pipes escaped and newlines flattened.
    """
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render(
    fixtures: list[Fixture],
    findings: list[Finding],
    totals: dict[str, object],
    app_version: str,
    max_records: int,
    request_count: int,
) -> str:
    """Render the manifest.

    Args:
        fixtures: Every fixture written, in the order written.
        findings: Cases that produced no fixture.
        totals: Measured totals worth recording.
        app_version: The API version the capture ran against.
        max_records: The per-set record cap in force.
        request_count: How many GETs the run issued.

    Returns:
        str: The manifest markdown.
    """
    total_bytes = sum(f.size_bytes for f in fixtures)
    oversize = [f for f in fixtures if f.size_bytes > ONE_MEGABYTE]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        "# Fixture manifest",
        "",
        "Real API response bodies, captured from a live database and saved **verbatim** "
        "— byte-for-byte as the API returned them, with no reformatting, prettifying, "
        "trimming or key reordering. Regenerate with `uv run design-fixtures`.",
        "",
        "## Run",
        "",
        f"- **Generated:** {generated}",
        f"- **API version:** {app_version}",
        f"- **Requests issued:** {request_count}, all GET",
        f"- **Fixtures written:** {len(fixtures)}",
        f"- **Total size:** {human_size(total_bytes)} ({total_bytes:,} bytes)",
        f"- **Per-set record cap:** {max_records}",
        "",
    ]

    if oversize:
        lines += ["### Fixtures over 1 MB", ""]
        for fixture in oversize:
            lines.append(
                f"- `{fixture.filename}` — {human_size(fixture.size_bytes)}. "
                f"Left whole rather than truncated."
            )
        lines.append("")
    else:
        lines += ["No single fixture exceeds 1 MB.", ""]

    if totals:
        lines += ["### Measured totals", ""]
        for key, value in totals.items():
            if key.startswith("_"):
                continue
            lines.append(f"- `{key}`: {value}")
        lines.append("")

    lines += [
        "## Fixtures",
        "",
        "| File | What it is | How it was selected | Records |",
        "|---|---|---|---|",
    ]
    for fixture in fixtures:
        count = "—" if fixture.record_count is None else str(fixture.record_count)
        status = "" if fixture.status == 200 else f" _(HTTP {fixture.status})_"
        lines.append(
            f"| `{fixture.filename}` "
            f"| {_escape(fixture.description)}{status} "
            f"| {_escape(fixture.selection)} "
            f"| {count} |"
        )
    lines.append("")

    notes = [f for f in fixtures if f.note]
    if notes:
        lines += ["## Notes", ""]
        for fixture in notes:
            lines.append(f"- **`{fixture.filename}`** — {fixture.note}")
        lines.append("")

    lines += ["## Cases with no matching data", ""]
    if findings:
        for finding in findings:
            lines.append(
                f"- **Case {finding.case} — {finding.description}** "
                f"Looked for: {finding.selection} **Result:** {finding.note}"
            )
    else:
        lines.append("Every case matched data and produced at least one fixture.")
    lines.append("")

    return "\n".join(lines)
