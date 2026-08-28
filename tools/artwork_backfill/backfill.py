"""Register the cover files already sitting in the accessory store as artwork.

Before #102 an asset's artwork was a file named ``cover.*`` in its accessory
directory and nothing else -- no row, no type, no provenance. Those files are still
there and still the only artwork most of the catalogue has, so the artwork table
starts empty and the browse grid stays blank until something imports them.

**The walk is over asset rows, not over the accessory tree.** That is the whole answer
to bounding it: the work is fixed at one directory probe per asset, there is no
recursion, and an unexpected directory in the store cannot lead anywhere. #54 was an
uncapped recursive walk over the inbox; this avoids the shape rather than capping it.
A cover whose asset row has since been deleted is skipped by construction, which is
correct -- there is nothing to attach it to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ArtworkORM, AssetORM
from app.repositories.artwork_repository import SQLAlchemyArtworkRepository
from app.repositories.errors import UniqueViolation
from app.schemas import ArtworkCreateInternal
from app.schemas.enums import EntityTypeEnum
from app.services.artwork_storage import ArtworkStore, StoredArtwork
from app.utils.paths import accessory_relative_path

#: The artwork kind a `cover.*` file represents. The producing runners write one image
#: per asset and it is the poster in every case.
COVER_KIND = "poster"

#: Suffixes a cover may carry, in the order they are tried. The first four are what
#: the producing runners actually write; the last two are accepted because the store
#: supports them and a hand-placed file may use either.
COVER_SUFFIXES: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")

#: How many asset ids to pull per query. Large enough that the round trip is amortised,
#: small enough that the id list never becomes the memory cost of the pass.
_ID_BATCH = 500


@dataclass
class Summary:
    """What a pass did, in enough detail to tell "nothing to do" from "did nothing"."""

    assets_scanned: int = 0
    covers_found: int = 0
    registered: int = 0
    already_registered: int = 0
    no_cover: int = 0
    failed: int = 0
    #: Refusal reason -> count, e.g. "not an image" or "too large".
    skipped: dict[str, int] = field(default_factory=dict)
    limit_reached: bool = False
    dry_run: bool = True

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())


def _iter_asset_ids(session: Session, after: int = 0) -> Iterator[int]:
    """Yield every asset id in ascending order, a batch at a time.

    Keyset rather than OFFSET: the pass commits as it goes, so an offset-paged walk
    would be reading a moving table.

    Args:
        session: The session to query through.
        after: Resume from the first id greater than this.

    Yields:
        int: Each asset id.
    """
    cursor = after
    while True:
        ids = list(
            session.scalars(
                select(AssetORM.id)
                .where(AssetORM.id > cursor)
                .order_by(AssetORM.id)
                .limit(_ID_BATCH)
            )
        )
        if not ids:
            return
        yield from ids
        cursor = ids[-1]


def _assets_with_artwork(session: Session, kind_id: int) -> set[int]:
    """Every asset id that already has artwork of this kind.

    Collected in one query rather than asked per asset. At 13,329 assets a per-asset
    existence check is 13,329 round trips, which at the measured 26ms baseline is most
    of an hour spent establishing that a re-run has nothing to do.

    Args:
        session: The session to query through.
        kind_id: The artwork kind to look for.

    Returns:
        set[int]: Asset ids already carrying that kind of artwork.
    """
    return set(
        session.scalars(
            select(ArtworkORM.entity_id).where(
                ArtworkORM.entity_type == EntityTypeEnum.asset,
                ArtworkORM.artwork_kind_id == kind_id,
            )
        )
    )


def find_cover(accessory_root: Path, asset_id: int) -> Path | None:
    """Locate an asset's cover file in the accessory store.

    Args:
        accessory_root: ACCESSORY_ROOT.
        asset_id: The asset to look under.

    Returns:
        Path | None: The cover file, or None if the asset has none.
    """
    directory = accessory_root / accessory_relative_path(asset_id)
    for suffix in COVER_SUFFIXES:
        candidate = directory / f"cover{suffix}"
        if candidate.is_file():
            return candidate
    return None


def run(
    session: Session,
    store: ArtworkStore,
    accessory_root: Path,
    kind_id: int,
    *,
    dry_run: bool = True,
    limit: int = 0,
    on_event: Callable[[str], None] | None = None,
) -> Summary:
    """Walk every asset and register the cover it already has.

    The accessory file is deliberately **left in place**. ``cover.*`` is a shared
    contract with the producing runners, which check for an existing cover before
    fetching one; moving it would make every producer re-download artwork it had
    already fetched. This copies out rather than moving, and the duplication is a few
    tens of kilobytes per asset.

    One unreadable or corrupt file must never end a pass over thousands, so every
    per-asset failure is counted and the walk continues. Nothing stops early on a run
    of already-registered assets either: covered and uncovered assets are interleaved
    arbitrarily, so a stretch of one says nothing about the next.

    **Continuing after a failed insert requires rolling the session back**, which is
    why every failure path does. SQLAlchemy leaves a session in a failed state after a
    failed flush, so without it the next statement raises ``PendingRollbackError``
    rather than doing its work -- including the id iterator's next batch query, which
    is issued outside the per-asset ``try``. The pass would then die on the batch
    boundary with a traceback naming the rollback, having attributed every failure
    after the first to the wrong cause. Counting a failure and carrying on is only
    real if the session is usable afterwards.

    Args:
        session: A session to read assets and write artwork through.
        store: Where artwork files are written.
        accessory_root: ACCESSORY_ROOT, where the covers currently live.
        kind_id: ID of the artwork kind to register covers as.
        dry_run: Report what would happen without writing files or rows.
        limit: Stop after attempting this many candidates, or 0 for no limit. Counts
            every asset whose cover was opened and acted on -- registered, failed, or
            found already present -- not successful registrations alone. Assets with
            no cover, and those the pre-load already knows are covered, are skipped
            before any work happens and do not count against it.
        on_event: Optional ``callable(str)`` for per-asset progress lines.

    Returns:
        Summary: What the pass found and did.
    """
    summary = Summary(dry_run=dry_run)
    already = _assets_with_artwork(session, kind_id)
    emit = on_event
    attempted = 0

    for asset_id in _iter_asset_ids(session):
        summary.assets_scanned += 1

        if asset_id in already:
            # Skipped before the file is even looked for: a re-run must be cheap, and
            # an asset that already has a poster may have had it chosen deliberately.
            # Re-registering a *changed* cover is out of scope -- it would overwrite a
            # curated choice with whatever a producer last wrote.
            summary.already_registered += 1
            continue

        cover = find_cover(accessory_root, asset_id)
        if cover is None:
            summary.no_cover += 1
            continue
        summary.covers_found += 1

        try:
            with cover.open("rb") as handle:
                # inspect() on a dry run so the reported scope is one the real run
                # will actually deliver -- same cap, same sniffing, no write.
                stored = store.inspect(handle) if dry_run else store.store(handle)
        except HTTPException as e:
            # ArtworkStore speaks HTTP because its other caller is a route. Reading
            # the status back is not elegant, but duplicating the format and size
            # rules here so a CLI could have its own exception type would be worse:
            # the two would drift, and this pass would accept files the API refuses.
            summary.skip(_reason(e))
            continue
        except OSError as e:
            summary.failed += 1
            if emit:
                emit(f"asset {asset_id}: could not read {cover}: {e}")
            continue

        # Counted before the attempt rather than after a successful one. `--limit` is
        # what bounds a first run against real data, and a run whose writes are all
        # failing is exactly when that bound matters -- so it cannot be spent only by
        # successes, and a failure cannot `continue` past the check.
        attempted += 1

        if dry_run:
            summary.registered += 1
            if emit:
                emit(f"asset {asset_id}: would register {cover.name} -> {stored.storage_path}")
        else:
            try:
                _insert(session, asset_id, kind_id, stored)
            except UniqueViolation:
                # Another writer got there between the pre-load and now, or the same
                # digest is already registered for this asset. Either way the row
                # exists, which is the outcome this pass wanted.
                session.rollback()
                summary.already_registered += 1
            except Exception as e:  # noqa: BLE001 - one bad row must not end the pass
                session.rollback()
                summary.failed += 1
                if emit:
                    emit(f"asset {asset_id}: could not register {cover}: {e}")
            else:
                summary.registered += 1
                if emit:
                    emit(f"asset {asset_id}: registered {cover.name} -> {stored.storage_path}")

        if limit and attempted >= limit:
            summary.limit_reached = True
            break

    return summary


def _reason(error: HTTPException) -> str:
    """Turn a store refusal into a summary bucket."""
    return {
        400: "empty file",
        413: "over the size cap",
        415: "not a supported image",
    }.get(error.status_code, f"refused ({error.status_code})")


def _insert(session: Session, asset_id: int, kind_id: int, stored: StoredArtwork) -> None:
    """Write one artwork row, committing it on its own.

    Committed per row rather than per pass so an interrupted run keeps everything it
    had already done and a resumed run skips it. A single transaction over 13,329
    assets would lose all of it to one failure at the end.
    """
    repo = SQLAlchemyArtworkRepository(session)
    repo.create(
        ArtworkCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset_id,
            artwork_kind_id=kind_id,
            storage_path=stored.storage_path,
            mime=stored.mime,
            width=None,
            height=None,
            # The only artwork this asset has, so it is the one to use. The pre-load
            # guarantees there is no incumbent primary of this kind to collide with.
            is_primary=True,
            source_scheme_id=None,
            source_external_id=None,
            # Nothing is known about where these came from beyond "it was on disk",
            # and inventing a provenance would be worse than recording none.
            source_url=None,
        )
    )
