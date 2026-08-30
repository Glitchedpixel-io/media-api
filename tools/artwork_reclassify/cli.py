"""Command-line entry point for the artwork reclassification pass.

    uv run artwork-reclassify              # dry run: report only
    uv run artwork-reclassify --apply

**Dry run is the default and ``--apply`` is required to write anything**, for the same
reason as the backfill and the dimensions pass: a maintenance pass that writes by default
is one keystroke from doing so against the wrong database, and this one is normally
pointed at production.

Deliberately a reviewed step rather than a migration, following ``6b1f8ac340d9``, which
left the edition classification to a script for the same reason: this rewrites the
classification of every row in the table, and a deploy is the wrong place to hold a
decision someone should read the output of first.

Configuration comes from the environment the app itself reads -- ``DATABASE_URL`` -- so
the pass cannot end up rewriting a different database than the API serves.
"""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_config

from .reclassify import SOURCE_KIND, Summary, distribution, run


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="artwork-reclassify",
        description=(
            f"Move '{SOURCE_KIND}' artwork onto the kind its measured size says it is. "
            "Reports without changing anything unless --apply is given."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the new kinds. Without it this is a dry run.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a line per artwork row as it is handled.",
    )
    return parser


def _render(summary: Summary, before: dict[str, int], after: dict[str, int]) -> str:
    """Format the summary, including what it did to the table as a whole."""
    lines = [
        "",
        "DRY RUN - nothing was written" if summary.dry_run else "Applied",
        f"  scanned             {summary.scanned}",
        f"  reclassified        {summary.moved_total}",
    ]
    for transition, count in sorted(summary.moved.items()):
        lines.append(f"    {transition:<28}{count}")

    if summary.unmapped:
        lines.append(f"  left alone          {summary.unmapped_total}")
        for size, count in sorted(summary.unmapped.items()):
            lines.append(f"    {size:<28}{count}")
        lines.append(
            "  These sizes are not in the mapping, so nothing was assumed about them. "
            "A genuine poster uploaded since #153 belongs here and should stay."
        )

    lines.append("  distribution")
    for code in sorted(set(before) | set(after)):
        lines.append(f"    {code:<28}{before.get(code, 0):>6} -> {after.get(code, 0):>6}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the reclassification pass.

    Returns:
        int: 0 on a clean pass, 1 if any row was left unmapped. Left-alone rows are not
            a failure of the pass, but they are the one outcome that wants a human to
            look, so a scheduled invocation should not report success and hide them.
    """
    args = build_parser().parse_args(argv)

    config = get_config()
    engine = create_engine(config.database.url, future=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        before = distribution(session)
        print(
            f"{'DRY RUN - ' if not args.apply else ''}"
            f"Reclassifying '{SOURCE_KIND}' artwork by measured size"
        )

        summary = run(session, dry_run=not args.apply, on_event=print if args.verbose else None)
        after = distribution(session)
    finally:
        session.close()
        engine.dispose()

    print(_render(summary, before, after))
    return 1 if summary.unmapped else 0


if __name__ == "__main__":
    raise SystemExit(main())
