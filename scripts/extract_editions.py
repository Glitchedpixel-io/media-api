#!/usr/bin/env python3
"""Extract edition markers from asset filenames, for review before they are applied.

Two steps on purpose (#92). The default run writes a report and changes nothing; a human
reads it; ``--apply`` then writes the reviewed classification. The parser is guessing from
a filename convention that also produces false positives -- a film genuinely called
"Uncut Gems" is the shape of the mistake -- so the report is the deliverable and the
write is an afterthought.

The report separates ``explicit`` markers, written deliberately as `{edition-...}` and
safe to accept in bulk, from ``inferred`` ones recognised from a convention that can
misfire. A reviewer reads the inferred rows and the unrecognised vocabulary; the explicit
ones need no attention.

Usage::

    # 1. Report only. Writes editions.csv and prints the summary.
    uv run python scripts/extract_editions.py

    # 2. Review editions.csv, deleting or correcting rows.

    # 3. Apply the reviewed file. Only ever fills nulls.
    uv run python scripts/extract_editions.py --apply --from-file editions.csv

The connection URL comes from ``ALEMBIC_DATABASE_URL`` or ``DATABASE_URL``, matching
``alembic/env.py``; it is never hardcoded.

**A rejection is not remembered.** Deleting a row from the CSV declines that guess, but
the asset stays null and the next run proposes it again -- null means both "has no
edition" and "not yet reviewed", and nothing here separates them. That is deliberate:
recording rejections needs a sentinel value or a second table, and neither is worth
carrying until the report is large enough that re-reading the same rejected rows actually
costs something. The report is the small end of the data by construction.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.editions import CANONICAL_EDITIONS, parse_edition  # noqa: E402

_FIELDS = ("asset_id", "filename", "edition", "raw_marker", "source", "canonical")


def _database_url() -> str:
    """The database to read, from the environment.

    Returns:
        str: A SQLAlchemy URL.

    Raises:
        SystemExit: If neither environment variable is set.
    """
    url = os.getenv("ALEMBIC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("Set ALEMBIC_DATABASE_URL or DATABASE_URL")
    return url


def report(engine: sa.Engine, out: Path) -> int:
    """Parse every asset filename and write the candidates to a CSV.

    Only assets whose edition is still null are considered, so a second run does not
    propose overwriting a reviewer's decision.

    Args:
        engine: Connection to the database to read.
        out: Path to write the CSV to.

    Returns:
        int: Process exit code.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT id, filename FROM assets WHERE edition IS NULL ORDER BY id")
        ).all()

    counts: Counter[str] = Counter()
    unknown_vocabulary: Counter[str] = Counter()
    candidates = []
    for asset_id, filename in rows:
        match = parse_edition(filename or "")
        if match is None:
            counts["no marker"] += 1
            continue
        counts[f"{match.source.value}, {'known' if match.canonical else 'unrecognised'}"] += 1
        if not match.canonical:
            unknown_vocabulary[match.value] += 1
        candidates.append(
            {
                "asset_id": asset_id,
                "filename": filename,
                "edition": match.value,
                "raw_marker": match.raw,
                "source": match.source.value,
                "canonical": "yes" if match.canonical else "no",
            }
        )

    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(candidates)

    total = len(rows)
    unclassified = counts["no marker"]
    print(f"\n{total} asset(s) with no edition recorded\n")
    for label, n in sorted(counts.items()):
        print(f"  {n:6d}  {label}")
    # The number #92 actually asks for. Reported as a share because "could not classify"
    # is only tractable to review if it is a minority; if it is most of the library the
    # convention is not one this parser can read and the approach needs rethinking.
    share = (unclassified / total * 100) if total else 0.0
    print(f"\n  {unclassified} of {total} ({share:.1f}%) carry no marker this parser recognises")

    if unknown_vocabulary:
        print("\n  Markers outside the canonical vocabulary -- candidates to add:")
        for value, n in unknown_vocabulary.most_common():
            print(f"    {n:6d}  {value}")

    print(f"\nWrote {len(candidates)} candidate(s) to {out}")
    print("Review it, then re-run with --apply --from-file to write the reviewed rows.")
    return 0


def apply(engine: sa.Engine, source: Path) -> int:
    """Write a reviewed CSV back to the database.

    Fills nulls only. A row whose asset already has an edition is skipped and counted,
    so applying an out-of-date review cannot quietly overwrite a later correction.

    Args:
        engine: Connection to the database to write.
        source: The reviewed CSV.

    Returns:
        int: Process exit code.
    """
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    written = skipped = 0
    with engine.begin() as conn:
        for row in rows:
            value = (row.get("edition") or "").strip()
            if not value:
                continue
            result = conn.execute(
                sa.text("UPDATE assets SET edition = :e WHERE id = :i AND edition IS NULL"),
                {"e": value, "i": int(row["asset_id"])},
            )
            if result.rowcount:
                written += 1
            else:
                skipped += 1

    print(f"Applied {written} edition(s); skipped {skipped} already set or missing")
    unknown = {r["edition"] for r in rows if r.get("edition")} - set(CANONICAL_EDITIONS)
    if unknown:
        print(f"Values outside the canonical vocabulary were written: {sorted(unknown)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the reviewed CSV back")
    parser.add_argument(
        "--from-file",
        type=Path,
        default=Path("editions.csv"),
        help="CSV path (default editions.csv)",
    )
    args = parser.parse_args()

    engine = sa.create_engine(_database_url())
    if args.apply:
        if not args.from_file.exists():
            raise SystemExit(f"No such file: {args.from_file}. Run without --apply first.")
        return apply(engine, args.from_file)
    return report(engine, args.from_file)


if __name__ == "__main__":
    raise SystemExit(main())
