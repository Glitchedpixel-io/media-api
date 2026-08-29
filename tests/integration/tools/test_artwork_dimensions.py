"""Integration coverage for the artwork dimensions pass.

Runs against a real database and real image files, because the things worth proving
are the ones a mocked version would assert into existence: that the numbers written are
the file's actual dimensions, that a row whose file is missing is reported rather than
guessed at, and that a re-run does the remainder rather than the lot.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.repositories import SQLAlchemyArtworkRepository, SQLAlchemyMediaRepository
from app.schemas import ArtworkCreateInternal, AssetCreateInternal
from app.schemas.enums import EntityTypeEnum
from app.services.artwork_storage import ArtworkStore
from app.utils.images import measure
from tools.artwork_dimensions.dimensions import count_needing_dimensions, run


def _image_bytes(width: int, height: int, fmt: str = "JPEG") -> bytes:
    """A real image of a known size, so the assertions are about measurement."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def artwork_root(test_settings: AppConfig) -> Path:
    root = Path(test_settings.media.artwork_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def store(test_settings: AppConfig) -> ArtworkStore:
    return ArtworkStore(test_settings.media)


@pytest.fixture
def poster_kind_id(artwork_kind_ids: dict[str, int]) -> int:
    return artwork_kind_ids["poster"]


@pytest.fixture
def make_artwork(db_session: Session, store: ArtworkStore, poster_kind_id: int):
    """Store a real image and register an artwork row for it, with no dimensions."""
    media_repo = SQLAlchemyMediaRepository(db_session)
    artwork_repo = SQLAlchemyArtworkRepository(db_session)
    counter = {"n": 0}

    def _make(width: int = 640, height: int = 960, fmt: str = "JPEG") -> int:
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
        stored = store.store(io.BytesIO(_image_bytes(width, height, fmt)))
        return artwork_repo.create(
            ArtworkCreateInternal(
                entity_type=EntityTypeEnum.asset,
                entity_id=asset.id,
                artwork_kind_id=poster_kind_id,
                storage_path=stored.storage_path,
                mime=stored.mime,
                width=None,
                height=None,
                is_primary=True,
                source_scheme_id=None,
                source_external_id=None,
                source_url=None,
            )
        ).id

    return _make


def _row(db_session: Session, artwork_id: int):
    return SQLAlchemyArtworkRepository(db_session).get(artwork_id)


@pytest.mark.integration
class TestMeasure:

    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF"])
    def test_reads_the_real_size_of_every_format_the_store_admits(self, tmp_path, fmt):
        path = tmp_path / "image"
        path.write_bytes(_image_bytes(321, 123, fmt))
        assert measure(path) == (321, 123)

    def test_raises_on_something_that_is_not_an_image(self, tmp_path):
        path = tmp_path / "not-an-image"
        path.write_bytes(b"<!doctype html>" + b" " * 64)
        with pytest.raises(OSError):
            measure(path)


@pytest.mark.integration
class TestDryRun:

    def test_a_dry_run_writes_nothing(self, db_session, artwork_root, make_artwork):
        artwork_id = make_artwork(800, 1200)

        summary = run(db_session, artwork_root, dry_run=True)

        assert summary.measured == 1
        assert _row(db_session, artwork_id).width is None

    def test_a_dry_run_still_reports_what_it_could_not_read(
        self, db_session, artwork_root, make_artwork
    ):
        """Otherwise the reported scope is one the real run will not deliver."""
        artwork_id = make_artwork()
        (artwork_root / _row(db_session, artwork_id).storage_path).write_bytes(b"junk" * 16)

        summary = run(db_session, artwork_root, dry_run=True)

        assert summary.measured == 0
        assert summary.skipped == {"not an image Pillow can read": 1}


@pytest.mark.integration
class TestApply:

    def test_records_the_files_real_dimensions(self, db_session, artwork_root, make_artwork):
        artwork_id = make_artwork(1280, 720)

        summary = run(db_session, artwork_root, dry_run=False)

        assert summary.measured == 1
        row = _row(db_session, artwork_id)
        assert (row.width, row.height) == (1280, 720)

    def test_rows_that_already_have_dimensions_are_not_visited(
        self, db_session, artwork_root, make_artwork
    ):
        make_artwork(640, 960)
        run(db_session, artwork_root, dry_run=False)

        second = run(db_session, artwork_root, dry_run=False)

        assert second.artwork_scanned == 0
        assert second.measured == 0

    def test_remeasure_revisits_rows_that_already_have_dimensions(
        self, db_session, artwork_root, make_artwork
    ):
        """The escape hatch for a pass that recorded something wrong."""
        artwork_id = make_artwork(1280, 720)
        run(db_session, artwork_root, dry_run=False)

        summary = run(db_session, artwork_root, dry_run=False, remeasure=True)

        assert summary.measured == 1
        row = _row(db_session, artwork_id)
        assert (row.width, row.height) == (1280, 720)

    def test_only_the_outstanding_rows_are_visited_on_a_resumed_pass(
        self, db_session, artwork_root, make_artwork
    ):
        for _ in range(3):
            make_artwork()

        run(db_session, artwork_root, dry_run=False, limit=1)
        second = run(db_session, artwork_root, dry_run=False)

        assert second.artwork_scanned == 2
        assert second.measured == 2


@pytest.mark.integration
class TestResilience:

    def test_a_row_whose_file_is_missing_is_reported_not_guessed(
        self, db_session, artwork_root, make_artwork
    ):
        """A row pointing at a file that is not there is a real inconsistency, and
        inventing dimensions for it would bury exactly that."""
        artwork_id = make_artwork()
        (artwork_root / _row(db_session, artwork_id).storage_path).unlink()

        summary = run(db_session, artwork_root, dry_run=False)

        assert summary.file_missing == 1
        assert summary.measured == 0
        assert _row(db_session, artwork_id).width is None

    def test_one_unreadable_file_does_not_end_the_pass(
        self, db_session, artwork_root, make_artwork
    ):
        bad = make_artwork()
        good = make_artwork(500, 400)
        (artwork_root / _row(db_session, bad).storage_path).write_bytes(b"junk" * 16)

        summary = run(db_session, artwork_root, dry_run=False)

        assert summary.skipped == {"not an image Pillow can read": 1}
        assert summary.measured == 1
        assert (_row(db_session, good).width, _row(db_session, good).height) == (500, 400)

    def test_a_failed_write_does_not_poison_the_rest_of_the_pass(
        self, db_session, artwork_root, make_artwork, monkeypatch
    ):
        """Same requirement as the backfill: counting a failure and carrying on is only
        real if the session is rolled back, or the next statement raises instead."""
        first = make_artwork(100, 200)
        second = make_artwork(300, 400)

        real_update = SQLAlchemyArtworkRepository.update
        calls = {"n": 0}

        def failing_once(self, artwork_id, update):
            calls["n"] += 1
            if calls["n"] == 1:
                # A statement that genuinely fails, rather than a bare raise: the bug
                # this guards is the session's state after a database error.
                self.db.execute(sql_text("SELECT * FROM a_table_that_does_not_exist"))
            return real_update(self, artwork_id, update)

        monkeypatch.setattr(SQLAlchemyArtworkRepository, "update", failing_once)

        summary = run(db_session, artwork_root, dry_run=False)

        assert summary.failed == 1
        assert summary.measured == 1
        assert _row(db_session, first).width is None
        assert (_row(db_session, second).width, _row(db_session, second).height) == (300, 400)


@pytest.mark.integration
class TestLimit:

    def test_limit_stops_the_pass_and_says_so(self, db_session, artwork_root, make_artwork):
        for _ in range(3):
            make_artwork()

        summary = run(db_session, artwork_root, dry_run=False, limit=2)

        assert summary.measured == 2
        assert summary.limit_reached is True

    def test_a_complete_pass_does_not_claim_it_was_truncated(
        self, db_session, artwork_root, make_artwork
    ):
        make_artwork()
        summary = run(db_session, artwork_root, dry_run=False, limit=5)
        assert summary.limit_reached is False

    def test_the_limit_is_spent_by_unreadable_files_too(
        self, db_session, artwork_root, make_artwork
    ):
        """The bound has to hold on the run that needs bounding, as it does for the
        backfill: a pass where nothing succeeds must still stop at N."""
        for _ in range(4):
            artwork_id = make_artwork()
            (artwork_root / _row(db_session, artwork_id).storage_path).write_bytes(b"junk" * 16)

        summary = run(db_session, artwork_root, dry_run=False, limit=2)

        assert summary.measured == 0
        assert summary.skipped == {"not an image Pillow can read": 2}
        assert summary.limit_reached is True

    def test_a_missing_file_does_not_spend_the_limit(self, db_session, artwork_root, make_artwork):
        """It cost nothing to find, so it must not buy the pass one fewer real row."""
        missing = make_artwork()
        (artwork_root / _row(db_session, missing).storage_path).unlink()
        readable = make_artwork(220, 330)

        summary = run(db_session, artwork_root, dry_run=False, limit=1)

        assert summary.file_missing == 1
        assert summary.measured == 1
        assert (_row(db_session, readable).width, _row(db_session, readable).height) == (220, 330)


@pytest.mark.integration
class TestCount:

    def test_counts_only_rows_missing_a_dimension(self, db_session, artwork_root, make_artwork):
        make_artwork()
        make_artwork()
        assert count_needing_dimensions(db_session) == 2

        run(db_session, artwork_root, dry_run=False, limit=1)
        assert count_needing_dimensions(db_session) == 1
