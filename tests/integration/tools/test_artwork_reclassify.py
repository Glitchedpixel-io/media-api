"""Integration tests for the artwork reclassification pass (#155, closing #138).

The population this fixes is specific and measured: 1,200 rows all labelled `poster`,
none poster-shaped, at seven distinct sizes. So the tests are built from those sizes
rather than from invented ones -- a mapping that works on made-up data and not on the
data it exists for would be worse than no test.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.repositories import SQLAlchemyArtworkRepository, SQLAlchemyMediaRepository
from app.schemas import ArtworkCreateInternal, AssetCreateInternal
from app.schemas.enums import EntityTypeEnum
from tools.artwork_reclassify.reclassify import MAPPING, SOURCE_KIND, distribution, run

pytestmark = pytest.mark.integration


@pytest.fixture
def make_artwork(db_session: Session, artwork_kind_ids: dict[str, int]):
    """Create an artwork row of a given size and kind, as the backfill would have."""
    artwork_repo = SQLAlchemyArtworkRepository(db_session)
    media_repo = SQLAlchemyMediaRepository(db_session)
    counter = {"n": 0}

    def _make(width: int, height: int, kind: str = SOURCE_KIND) -> int:
        counter["n"] += 1
        n = counter["n"]
        asset = media_repo.create(
            AssetCreateInternal(
                path=f"movies/{n}.mkv",
                filename=f"{n}.mkv",
                duration=1.0,
                bitrate=1,
                container_format="matroska",
                size=1,
                mtime=None,
                last_seen=None,
                master_asset_id=None,
            )
        )
        digest = f"{n:064x}"
        return artwork_repo.create(
            ArtworkCreateInternal(
                entity_type=EntityTypeEnum.asset,
                entity_id=asset.id,
                artwork_kind_id=artwork_kind_ids[kind],
                storage_path=f"{digest[:2]}/{digest[2:4]}/{digest}.jpg",
                mime="image/jpeg",
                width=width,
                height=height,
                is_primary=True,
                source_scheme_id=None,
                source_external_id=None,
                source_url=None,
            )
        ).id

    return _make


def _kind_of(db_session: Session, artwork_id: int) -> str:
    return SQLAlchemyArtworkRepository(db_session).get(artwork_id).artwork_kind


class TestTheRealPopulation:
    """Every size this database actually holds, and what #127 decided each one is."""

    @pytest.mark.parametrize(
        ("width", "height", "expected"),
        [
            (1280, 720, "thumbnail"),
            (1920, 1080, "thumbnail"),
            (640, 480, "thumbnail"),
            (480, 360, "thumbnail"),
            (500, 500, "cover_art"),
            (499, 500, "cover_art"),
            (128, 96, "unknown"),
        ],
    )
    def test_each_measured_size_lands_on_its_decided_kind(
        self, db_session, make_artwork, width, height, expected
    ):
        artwork_id = make_artwork(width, height)

        run(db_session, dry_run=False)

        assert _kind_of(db_session, artwork_id) == expected

    def test_the_499x500_cover_is_not_treated_as_an_oddity(self, db_session, make_artwork):
        """One real row is 0.2% off square. It is a genuine cover, so it belongs with
        the others rather than in `unknown` -- the same row that justified the
        tolerance in #151."""
        artwork_id = make_artwork(499, 500)

        run(db_session, dry_run=False)

        assert _kind_of(db_session, artwork_id) == "cover_art"

    def test_the_tiny_row_becomes_unknown_rather_than_a_bad_thumbnail(
        self, db_session, make_artwork
    ):
        """128x96 is below any usable floor, so the honest answer is that nobody knows
        what it is -- not that it is a small thumbnail."""
        artwork_id = make_artwork(128, 96)

        run(db_session, dry_run=False)

        assert _kind_of(db_session, artwork_id) == "unknown"

    def test_nothing_is_left_claiming_to_be_a_poster(self, db_session, make_artwork):
        """The outcome #138 asks for: we hold no portrait artwork, so after this pass
        nothing claims to."""
        for width, height in MAPPING:
            make_artwork(width, height)

        run(db_session, dry_run=False)

        assert distribution(db_session).get(SOURCE_KIND, 0) == 0


class TestDryRun:

    def test_a_dry_run_writes_nothing(self, db_session, make_artwork):
        artwork_id = make_artwork(1280, 720)

        summary = run(db_session, dry_run=True)

        assert summary.moved_total == 1
        assert _kind_of(db_session, artwork_id) == SOURCE_KIND

    def test_a_dry_run_reports_the_same_transitions_the_real_run_makes(
        self, db_session, make_artwork
    ):
        """A dry run whose scope differs from the write is worse than none: it is the
        output someone approves the real run on."""
        make_artwork(1280, 720)
        make_artwork(500, 500)

        planned = run(db_session, dry_run=True)
        applied = run(db_session, dry_run=False)

        assert planned.moved == applied.moved


class TestUnmappedSizes:
    """A size nobody measured is reported, never guessed at."""

    def test_an_unmapped_size_is_left_alone(self, db_session, make_artwork):
        """Since #153 a genuine portrait poster can be uploaded. Moving one would undo
        the thing this pass exists to achieve."""
        artwork_id = make_artwork(600, 900)

        summary = run(db_session, dry_run=False)

        assert _kind_of(db_session, artwork_id) == SOURCE_KIND
        assert summary.unmapped == {"600x900": 1}

    def test_an_unmapped_row_does_not_stop_the_mapped_ones(self, db_session, make_artwork):
        mapped = make_artwork(1280, 720)
        make_artwork(600, 900)

        summary = run(db_session, dry_run=False)

        assert _kind_of(db_session, mapped) == "thumbnail"
        assert summary.moved_total == 1
        assert summary.unmapped_total == 1


class TestScope:

    def test_other_kinds_are_not_touched(self, db_session, make_artwork):
        """Only `poster` rows have established provenance. An `unknown` row carries no
        claim precisely because nobody knows what it is, and reclassifying it by shape
        is the inference #127 ruled out."""
        unknown = make_artwork(1280, 720, kind="unknown")

        run(db_session, dry_run=False)

        assert _kind_of(db_session, unknown) == "unknown"

    def test_a_second_pass_is_a_no_op(self, db_session, make_artwork):
        make_artwork(1280, 720)
        run(db_session, dry_run=False)

        second = run(db_session, dry_run=False)

        assert second.scanned == 0
        assert second.moved_total == 0

    def test_an_empty_table_is_not_an_error(self, db_session):
        summary = run(db_session, dry_run=False)
        assert summary.scanned == 0
