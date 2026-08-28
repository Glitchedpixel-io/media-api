"""Command-line entry point for the artwork backfill.

    uv run artwork-backfill                 # dry run: report, change nothing
    uv run artwork-backfill --apply --limit 20
    uv run artwork-backfill --apply

**Dry run is the default and ``--apply`` is required to write anything.** A backfill
that writes by default is one keystroke from doing so against the wrong database, and
this one is normally pointed at production.

Configuration comes from the environment the app itself reads -- ``DATABASE_URL``,
``ACCESSORY_ROOT`` and ``ARTWORK_ROOT`` -- so the pass cannot end up looking at a
different store than the API serves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_config
from app.repositories.artwork_repository import SQLAlchemyArtworkKindRepository
from app.services.artwork_storage import ArtworkStore

from .backfill import COVER_KIND, Summary, run


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="artwork-backfill",
        description=(
            "Register the cover files already in the accessory store as artwork. "
            "Reports without changing anything unless --apply is given."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write files and rows. Without it this is a dry run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Stop after attempting N covers, so a first real run is small enough to "
            "inspect. Counts attempts, not successes, so a run whose writes are all "
            "failing still stops at N."
        ),
    )
    parser.add_argument(
        "--kind",
        default=COVER_KIND,
        metavar="CODE",
        help=f"Artwork kind to register covers as (default: {COVER_KIND}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a line per asset as it is handled.",
    )
    return parser


def _render(summary: Summary) -> str:
    """Render the end-of-run summary.

    Every bucket is printed even when zero. A pass that found nothing and a pass that
    silently did nothing look identical if the report only lists non-zero counts --
    which is the defect #57 recorded, in a different guise.
    """
    mode = "DRY RUN - nothing was written" if summary.dry_run else "applied"
    lines = [
        "",
        f"Artwork backfill complete ({mode}).",
        f"  assets scanned      {summary.assets_scanned}",
        f"  covers found        {summary.covers_found}",
        f"  registered          {summary.registered}",
        f"  already registered  {summary.already_registered}",
        f"  no cover on disk    {summary.no_cover}",
        f"  skipped             {summary.skipped_total}",
    ]
    for reason, count in sorted(summary.skipped.items()):
        lines.append(f"    - {reason}: {count}")
    lines.append(f"  failed              {summary.failed}")

    if summary.limit_reached:
        lines += [
            "",
            "  NOT A COMPLETE PASS: stopped at --limit with assets still unvisited.",
            "  Re-run without --limit to finish; already-registered assets are skipped.",
        ]
    if summary.dry_run:
        lines += ["", "  Re-run with --apply to write these."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the backfill.

    Returns:
        int: 0 on a clean pass, 1 if any asset failed. A failure count above zero is
            an unsuccessful run even though the pass deliberately continued past it --
            exiting 0 would let a scheduled invocation hide a systematic problem.
    """
    args = build_parser().parse_args(argv)
    config = get_config()

    accessory_root = Path(config.media.accessory_root)
    if not accessory_root.is_dir():
        # Failing loudly beats reporting "0 covers found" for a root that was never
        # mounted -- the two are indistinguishable in the summary otherwise.
        print(
            f"ACCESSORY_ROOT does not exist or is not a directory: {accessory_root}",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(config.database.url, future=True)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        kind = SQLAlchemyArtworkKindRepository(session).get_by_code(args.kind)
        if kind is None:
            print(
                f"Unknown artwork kind '{args.kind}'. "
                "Has `alembic upgrade head` run against this database?",
                file=sys.stderr,
            )
            return 2

        print(
            f"{'DRY RUN - ' if not args.apply else ''}"
            f"Backfilling '{args.kind}' artwork from {accessory_root} "
            f"into {config.media.artwork_root}"
        )

        summary = run(
            session,
            ArtworkStore(config.media),
            accessory_root,
            kind.id,
            dry_run=not args.apply,
            limit=args.limit,
            on_event=print if args.verbose else None,
        )
    finally:
        session.close()
        engine.dispose()

    print(_render(summary))
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
