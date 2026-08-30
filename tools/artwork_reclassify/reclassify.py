"""Move the mislabelled artwork rows onto the kinds they actually are.

Every artwork row in this catalogue is labelled ``poster`` and **none is poster-shaped**
(#138). #127 settled what the kinds mean, which makes the correction decidable -- and,
because the stored sizes cluster tightly, almost entirely mechanical.

**Mapped on measured size, not on ratio.** Ratio alone cannot tell a 1280x720 thumbnail
from a 1920x1080 one, and more importantly it cannot say a 128x96 image is too small to
be useful artwork at all. The sizes are known exactly (#115, #143), so the mapping is a
lookup rather than an inference.

**Scoped to ``poster`` rows, deliberately.** Those are the rows whose provenance was
established: the backfill wrote all of them from ``cover.*`` files, and they were
measured before this mapping was written. An ``unknown`` row is a different case -- it
carries no claim precisely because nobody knows what it is, and reclassifying it by shape
would be the inference #127 ruled out. It stays unknown until something declares it.

A size the mapping does not cover is **reported and left alone**, never guessed at. That
is the case where a genuine poster has since been uploaded: after #153 the API accepts
portrait artwork as ``poster``, and this pass must not move it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ArtworkKindORM, ArtworkORM

#: The kind these rows currently claim to be, and the only kind this pass touches.
SOURCE_KIND = "poster"

#: Measured size -> the kind that size actually is, for the population the backfill
#: registered. Every entry is a size this database holds; nothing here is speculative.
#:
#: The 16:9 pair are the same image at two qualities rather than two kinds, and the 4:3
#: sizes are genuine thumbnails of SD-era content -- which is why ``thumbnail`` carries
#: no target ratio (#151) and can hold both. 128x96 is too small to be useful artwork of
#: any kind, so it becomes the absence of a claim rather than a bad one.
MAPPING: dict[tuple[int, int], str] = {
    (1280, 720): "thumbnail",
    (1920, 1080): "thumbnail",
    (640, 480): "thumbnail",
    (480, 360): "thumbnail",
    (500, 500): "cover_art",
    (499, 500): "cover_art",
    (128, 96): "unknown",
}


@dataclass
class Summary:
    """What a pass found and did."""

    dry_run: bool
    scanned: int = 0
    #: "poster -> thumbnail" etc, counted per transition so the report reads as the
    #: decision it implements rather than as a single total.
    moved: dict[str, int] = field(default_factory=dict)
    #: Measured size -> count, for rows the mapping does not cover. Reported rather
    #: than guessed at.
    unmapped: dict[str, int] = field(default_factory=dict)

    def move(self, target: str) -> None:
        key = f"{SOURCE_KIND} -> {target}"
        self.moved[key] = self.moved.get(key, 0) + 1

    def leave(self, width: int, height: int) -> None:
        key = f"{width}x{height}"
        self.unmapped[key] = self.unmapped.get(key, 0) + 1

    @property
    def moved_total(self) -> int:
        return sum(self.moved.values())

    @property
    def unmapped_total(self) -> int:
        return sum(self.unmapped.values())


def _kind_ids(session: Session) -> dict[str, int]:
    """Every artwork kind code mapped to its id."""
    return {
        code: kind_id
        for kind_id, code in session.execute(select(ArtworkKindORM.id, ArtworkKindORM.code))
    }


def distribution(session: Session) -> dict[str, int]:
    """Artwork kind code -> how many rows carry it.

    Reported before and after so an operator can see the pass's effect on the whole
    table rather than only on the rows it touched.
    """
    counts: dict[str, int] = {}
    rows = session.execute(
        select(ArtworkKindORM.code, ArtworkORM.id).join(
            ArtworkORM, ArtworkORM.artwork_kind_id == ArtworkKindORM.id
        )
    )
    for code, _artwork_id in rows:
        counts[code] = counts.get(code, 0) + 1
    return counts


def run(
    session: Session,
    *,
    dry_run: bool = True,
    on_event: Callable[[str], None] | None = None,
) -> Summary:
    """Reclassify every ``poster`` row whose measured size the mapping covers.

    One transaction rather than one per row, unlike the backfill: this is an update of
    a bounded, known set with no file I/O in it, so a partial application is worse than
    a clean failure. The backfill commits per row because a pass over 13,329 assets
    doing disk work must survive interruption; there is nothing here to resume.

    Args:
        session: A session to read and update artwork through.
        dry_run: Report what would change without writing anything.
        on_event: Optional ``callable(str)`` for per-row lines.

    Returns:
        Summary: What the pass found and did.

    Raises:
        LookupError: If a kind the mapping targets does not exist. That means #151 has
            not been applied, and reclassifying onto a kind that is not there would
            fail per row with nothing useful to say.
    """
    summary = Summary(dry_run=dry_run)
    kinds = _kind_ids(session)

    missing = sorted({target for target in MAPPING.values() if target not in kinds})
    if missing:
        raise LookupError(f"artwork kinds {missing} do not exist -- apply the #151 migration first")
    if SOURCE_KIND not in kinds:
        return summary

    rows = session.scalars(
        select(ArtworkORM).where(ArtworkORM.artwork_kind_id == kinds[SOURCE_KIND])
    ).all()

    for row in rows:
        summary.scanned += 1
        target = MAPPING.get((row.width, row.height))

        if target is None:
            # A size nobody measured when this mapping was written. Since #153 a real
            # poster can be uploaded, and moving one would undo the thing this pass
            # exists to achieve.
            summary.leave(row.width, row.height)
            if on_event:
                on_event(f"artwork {row.id}: {row.width}x{row.height} is not mapped, left alone")
            continue

        summary.move(target)
        if on_event:
            on_event(f"artwork {row.id}: {row.width}x{row.height} -> {target}")
        if not dry_run:
            row.artwork_kind_id = kinds[target]

    if not dry_run:
        session.commit()

    return summary
