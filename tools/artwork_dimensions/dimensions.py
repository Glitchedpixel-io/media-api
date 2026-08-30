"""Re-derive every artwork row's pixel dimensions from the file it points at.

Originally (#115) this filled in rows created without dimensions: the backfill passed
``width=None, height=None`` and uploads took the fields from the caller, so anything
registered before callers sent them had neither. That gap is closed -- #140 made the
API measure every upload, #141 made it record what it measured, and #143 made the
columns NOT NULL -- so there is nothing left to fill.

What remains is recovery. If a stored measurement is ever believed wrong, this derives
all of them again from the stored files. The pass therefore visits every row; there is
no "outstanding" subset to prefer, because one can no longer exist.

**The walk is over artwork rows, not over assets.** The backfill's shape is one probe
per asset, which is right for *finding* covers but wrong here: there are 13,329 assets
and roughly 1,200 artwork rows, so walking assets would spend more than 90% of the pass
on entities that can never contribute. The correction is to iterate the thing being
corrected.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
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


def count_artwork(session: Session) -> int:
    """How many artwork rows a pass would visit.

    Every row, because since #143 there are no unmeasured ones to single out.

    Args:
        session: The session to query through.

    Returns:
        int: The number of rows a pass would visit.
    """
    return len(list(session.scalars(select(ArtworkORM.id))))


def _iter_artwork(session: Session, *, after: int = 0) -> Iterator[tuple[int, str]]:
    """Yield ``(id, storage_path)`` for each artwork to visit, a batch at a time.

    Keyset rather than OFFSET. The original reason was that the pass removed rows from
    its own predicate as it went, so an offset-paged walk over a shrinking result set
    would skip rows; since #143 the predicate is "every row" and cannot shrink, but
    keyset is still the right shape for a walk that commits as it goes and may be
    resumed after an interruption.

    ``storage_path`` is selected alongside the id rather than fetched per row, which
    would double the query count for no benefit.

    Args:
        session: The session to query through.
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
    on_event: Callable[[str], None] | None = None,
) -> Summary:
    """Re-measure each artwork's stored file and record its dimensions.

    A recovery pass, not a backfill. Since #140 the API measures every upload and #143
    made the columns NOT NULL, so there are no unmeasured rows to find; this exists for
    the case where a previous measurement is believed wrong and every row needs
    deriving again.

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
        on_event: Optional ``callable(str)`` for per-row progress lines.

    Returns:
        Summary: What the pass found and did.
    """
    summary = Summary(dry_run=dry_run)
    repo = SQLAlchemyArtworkRepository(session)
    emit = on_event
    attempted = 0

    for artwork_id, storage_path in _iter_artwork(session):
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
