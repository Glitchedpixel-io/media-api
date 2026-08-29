"""Fill in the pixel dimensions that existing artwork rows were created without.

``tools/artwork_backfill`` registers what it finds on disk and passes ``width=None,
height=None`` -- it never opens the image to measure it. Uploads take the fields from
the caller, so anything registered before callers started sending them has neither.
The browse grid has no intrinsic size to lay out against for those rows.

**The walk is over artwork rows, not over assets.** The backfill's shape is one probe
per asset, which is right for *finding* covers but wrong here: there are 13,329 assets
and 1,194 artwork rows, so walking assets would spend more than 90% of the pass on
entities that can never contribute. The correction is to iterate the thing being
corrected.

By default it visits only rows missing a dimension, so a re-run after a partial pass
does the remainder rather than the lot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import ArtworkORM
from app.repositories.artwork_repository import SQLAlchemyArtworkRepository
from app.schemas.artwork import ArtworkUpdateInternal
from app.utils.images import measure

#: How many rows to pull per query. Matches the backfill's batch for the same reason:
#: large enough to amortise the round trip, small enough that the id list is not the
#: memory cost of the pass.
_ID_BATCH = 500


@dataclass
class Summary:
    """What a pass did, in enough detail to tell "nothing to do" from "did nothing"."""

    artwork_scanned: int = 0
    measured: int = 0
    file_missing: int = 0
    failed: int = 0
    #: Refusal reason -> count, e.g. "not an image Pillow can read".
    skipped: dict[str, int] = field(default_factory=dict)
    limit_reached: bool = False
    dry_run: bool = True

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())


def count_needing_dimensions(session: Session) -> int:
    """How many artwork rows are missing at least one dimension.

    Args:
        session: The session to query through.

    Returns:
        int: The number of rows a default pass would visit.
    """
    return len(
        list(
            session.scalars(
                select(ArtworkORM.id).where(
                    or_(ArtworkORM.width.is_(None), ArtworkORM.height.is_(None))
                )
            )
        )
    )


def _iter_artwork(
    session: Session, *, remeasure: bool, after: int = 0
) -> Iterator[tuple[int, str]]:
    """Yield ``(id, storage_path)`` for each artwork to visit, a batch at a time.

    Keyset rather than OFFSET, and for a sharper reason than usual: the default
    predicate is "has no width", which this pass *removes rows from as it goes*. An
    offset-paged walk over a shrinking result set skips rows -- page 2 of a set that
    lost 500 members is not the second 500 of the original. Walking forward by id is
    unaffected, because a row that drops out of the filter is one already behind the
    cursor.

    ``storage_path`` is selected alongside the id rather than fetched per row, which
    would double the query count for no benefit.

    Args:
        session: The session to query through.
        remeasure: Visit every row, including those that already have dimensions.
        after: Resume from the first id greater than this.

    Yields:
        tuple[int, str]: Each artwork's id and its path relative to ARTWORK_ROOT.
    """
    cursor = after
    while True:
        stmt = (
            select(ArtworkORM.id, ArtworkORM.storage_path)
            .where(ArtworkORM.id > cursor)
            .order_by(ArtworkORM.id)
            .limit(_ID_BATCH)
        )
        if not remeasure:
            stmt = stmt.where(or_(ArtworkORM.width.is_(None), ArtworkORM.height.is_(None)))

        rows = list(session.execute(stmt))
        if not rows:
            return
        for artwork_id, storage_path in rows:
            yield artwork_id, storage_path
        cursor = rows[-1][0]


def run(
    session: Session,
    artwork_root: Path,
    *,
    dry_run: bool = True,
    limit: int = 0,
    remeasure: bool = False,
    on_event: Callable[[str], None] | None = None,
) -> Summary:
    """Measure each artwork's stored file and record its dimensions.

    The file under ``ARTWORK_ROOT`` is measured rather than whatever the artwork was
    originally made from. The stored copy is what the row points at and what the API
    serves, so it is the thing whose dimensions the row is meant to describe -- and it
    is content addressed, so it cannot have drifted from what was registered.

    One unreadable file must not end a pass over thousands, so every per-row failure is
    counted and the walk continues. As in the backfill, that requires rolling the
    session back: a failed flush leaves it unusable, and the iterator's next batch query
    is issued outside the per-row ``try``.

    Nothing is written on a row whose file is missing. A row pointing at a file that is
    not there is a real inconsistency worth reporting, and guessing dimensions for it
    would bury that.

    Args:
        session: A session to read artwork and write dimensions through.
        artwork_root: ARTWORK_ROOT, where the stored files live.
        dry_run: Report what would happen without writing anything.
        limit: Stop after attempting this many rows, or 0 for no limit. Counts every
            row whose file was opened and acted on -- measured, skipped or failed. Rows
            whose file is absent cost nothing and do not count against it.
        remeasure: Visit rows that already carry dimensions, overwriting them.
        on_event: Optional ``callable(str)`` for per-row progress lines.

    Returns:
        Summary: What the pass found and did.
    """
    summary = Summary(dry_run=dry_run)
    repo = SQLAlchemyArtworkRepository(session)
    emit = on_event
    attempted = 0

    for artwork_id, storage_path in _iter_artwork(session, remeasure=remeasure):
        summary.artwork_scanned += 1

        path = artwork_root / storage_path
        if not path.is_file():
            summary.file_missing += 1
            if emit:
                emit(f"artwork {artwork_id}: no file at {storage_path}")
            continue

        attempted += 1

        try:
            width, height = measure(path)
        except OSError as e:
            summary.skip("not an image Pillow can read")
            if emit:
                emit(f"artwork {artwork_id}: could not measure {storage_path}: {e}")
        else:
            if dry_run:
                summary.measured += 1
                if emit:
                    emit(f"artwork {artwork_id}: would set {width}x{height}")
            else:
                try:
                    repo.update(artwork_id, ArtworkUpdateInternal(width=width, height=height))
                except Exception as e:  # noqa: BLE001 - one bad row must not end the pass
                    session.rollback()
                    summary.failed += 1
                    if emit:
                        emit(f"artwork {artwork_id}: could not record {width}x{height}: {e}")
                else:
                    summary.measured += 1
                    if emit:
                        emit(f"artwork {artwork_id}: set {width}x{height}")

        if limit and attempted >= limit:
            summary.limit_reached = True
            break

    return summary
