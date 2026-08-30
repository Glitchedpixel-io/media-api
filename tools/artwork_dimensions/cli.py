"""Command-line entry point for the artwork re-measurement pass.

A recovery tool, not a backfill. Since #140 the API measures every upload, and #143
made width and height NOT NULL, so there are no unmeasured rows left to find. This
exists for the case where a stored measurement is believed wrong and every row needs
deriving from its file again.

    uv run artwork-dimensions                     # dry run: report only
    uv run artwork-dimensions --apply --limit 20
    uv run artwork-dimensions --apply

**Dry run is the default and ``--apply`` is required to write anything**, for the same
reason as the backfill: a maintenance pass that writes by default is one keystroke from
doing so against the wrong database, and this one is normally pointed at production.

Configuration comes from the environment the app itself reads -- ``DATABASE_URL`` and
``ARTWORK_ROOT`` -- so the pass cannot end up measuring a different store than the API
serves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_config

from .dimensions import Summary, count_artwork, run


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="artwork-dimensions",
        description=(
            "Fill in the pixel dimensions of artwork rows that have none. "
            "Reports without changing anything unless --apply is given."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write dimensions. Without it this is a dry run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Stop after attempting N rows, so a first real run is small enough to "
            "inspect. Counts attempts, not successes."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a line per artwork as it is handled.",
    )
    return parser


def _render(summary: Summary) -> str:
    """Render the end-of-run summary.

    Every bucket is printed even when zero, so that a pass which found nothing to do
    and a pass which silently did nothing do not read identically.
    """
    mode = "DRY RUN - nothing was written" if summary.dry_run else "applied"
    lines = [
        "",
        f"Artwork dimensions pass complete ({mode}).",
        f"  artwork scanned     {summary.artwork_scanned}",
        f"  measured            {summary.measured}",
        f"  file missing        {summary.file_missing}",
        f"  skipped             {summary.skipped_total}",
    ]
    for reason, count in sorted(summary.skipped.items()):
        lines.append(f"    - {reason}: {count}")
    lines.append(f"  failed              {summary.failed}")

    if summary.file_missing:
        lines += [
            "",
            "  Rows whose stored file is absent were left untouched. That is an",
            "  inconsistency between the table and ARTWORK_ROOT, not a measuring",
            "  problem -- investigate before assuming the pass is done.",
        ]
    if summary.limit_reached:
        lines += [
            "",
            "  NOT A COMPLETE PASS: stopped at --limit with rows still unvisited.",
            "  Re-run without --limit to cover every row.",
        ]
    if summary.dry_run:
        lines += ["", "  Re-run with --apply to write these."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the dimensions pass.

    Returns:
        int: 0 on a clean pass, 1 if any row failed or any file was missing. Both are
            unsuccessful outcomes even though the pass deliberately continued past
            them -- exiting 0 would let a scheduled invocation hide a systematic
            problem, which is the same reasoning the backfill's exit code uses.
    """
    args = build_parser().parse_args(argv)

    config = get_config()

    artwork_root = Path(config.media.artwork_root)
    if not artwork_root.is_dir():
        # Failing loudly beats reporting "every file missing" for a root that was
        # never mounted -- the two are indistinguishable in the summary otherwise.
        print(
            f"ARTWORK_ROOT does not exist or is not a directory: {artwork_root}",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(config.database.url, future=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        total = count_artwork(session)
        print(
            f"{'DRY RUN - ' if not args.apply else ''}"
            f"Re-measuring {total} artwork row(s) against {artwork_root}"
        )

        summary = run(
            session,
            artwork_root,
            dry_run=not args.apply,
            limit=args.limit,
            on_event=print if args.verbose else None,
        )
    finally:
        session.close()
        engine.dispose()

    print(_render(summary))
    return 1 if (summary.failed or summary.file_missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
