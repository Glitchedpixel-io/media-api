"""Integration coverage for the artwork backfill.

Runs the real pass against a real database and a real accessory tree on disk. The
things worth proving are the ones a mocked version would assert into existence: that a
second run does nothing, that one corrupt file does not end the pass, and that a
truncated run says so rather than looking complete.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.repositories import SQLAlchemyArtworkRepository, SQLAlchemyMediaRepository
from app.repositories.errors import UniqueViolation
from app.schemas import AssetCreateInternal
from app.schemas.enums import EntityTypeEnum
from app.services.artwork_storage import MAX_ARTWORK_BYTES, ArtworkStore
from app.utils.paths import accessory_relative_path
from tools.artwork_backfill import backfill
from tools.artwork_backfill.backfill import find_cover, run

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def accessory_root(test_settings: AppConfig) -> Path:
    root = Path(test_settings.media.accessory_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def artwork_root(test_settings: AppConfig) -> Path:
    return Path(test_settings.media.artwork_root)


@pytest.fixture
def store(test_settings: AppConfig) -> ArtworkStore:
    return ArtworkStore(test_settings.media)


@pytest.fixture
def poster_kind_id(artwork_kind_ids: dict[str, int]) -> int:
    return artwork_kind_ids["poster"]


@pytest.fixture
def make_asset(db_session: Session):
    """Create an asset row and return its id."""
    repo = SQLAlchemyMediaRepository(db_session)
    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        n = counter["n"]
        return repo.create(
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
        ).id

    return _make


def _place_cover(accessory_root: Path, asset_id: int, payload: bytes, suffix: str = ".jpg") -> Path:
    """Put a cover file where the producing runners would have written it."""
    directory = accessory_root / accessory_relative_path(asset_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"cover{suffix}"
    path.write_bytes(payload)
    return path


def _artwork_for(db_session: Session, asset_id: int) -> list:
    return SQLAlchemyArtworkRepository(db_session).list_for_entity(EntityTypeEnum.asset, asset_id)


@pytest.mark.integration
class TestFindCover:

    def test_finds_a_cover_where_the_runners_write_it(self, accessory_root, make_asset):
        asset_id = make_asset()
        expected = _place_cover(accessory_root, asset_id, JPEG)
        assert find_cover(accessory_root, asset_id) == expected

    def test_returns_none_when_the_asset_has_no_cover(self, accessory_root, make_asset):
        assert find_cover(accessory_root, make_asset()) is None

    @pytest.mark.parametrize("suffix", [".jpg", ".jpeg", ".png", ".webp"])
    def test_accepts_every_suffix_the_producers_write(self, accessory_root, make_asset, suffix):
        asset_id = make_asset()
        _place_cover(accessory_root, asset_id, JPEG, suffix)
        assert find_cover(accessory_root, asset_id) is not None

    def test_ignores_other_accessory_files(self, accessory_root, make_asset):
        """The accessory directory is a shared working directory -- subtitles,
        chapters and transcode output live there too."""
        asset_id = make_asset()
        directory = accessory_root / accessory_relative_path(asset_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "subtitle.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        (directory / "chapters.json").write_bytes(b"{}")
        assert find_cover(accessory_root, asset_id) is None


@pytest.mark.integration
class TestDryRun:

    def test_a_dry_run_writes_no_file_and_no_row(
        self, db_session, store, accessory_root, artwork_root, poster_kind_id, make_asset
    ):
        asset_id = make_asset()
        _place_cover(accessory_root, asset_id, JPEG)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=True)

        assert summary.registered == 1
        assert list(artwork_root.rglob("*.jpg")) == []
        assert _artwork_for(db_session, asset_id) == []

    def test_a_dry_run_still_refuses_what_the_real_run_would(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """Otherwise the reported scope is one the real run will not deliver."""
        _place_cover(accessory_root, make_asset(), b"<!doctype html>" + b" " * 32)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=True)

        assert summary.registered == 0
        assert summary.skipped == {"not a supported image": 1}


@pytest.mark.integration
class TestApply:

    def test_registers_a_cover_and_writes_the_file(
        self, db_session, store, accessory_root, artwork_root, poster_kind_id, make_asset
    ):
        asset_id = make_asset()
        _place_cover(accessory_root, asset_id, JPEG)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert summary.registered == 1
        rows = _artwork_for(db_session, asset_id)
        assert len(rows) == 1
        assert rows[0].artwork_kind == "poster"
        assert rows[0].mime == "image/jpeg"
        assert (artwork_root / rows[0].storage_path).read_bytes() == JPEG

    def test_the_registered_artwork_is_primary(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """It is the only artwork the asset has, and dressing the grid is the point."""
        asset_id = make_asset()
        _place_cover(accessory_root, asset_id, JPEG)
        run(db_session, store, accessory_root, poster_kind_id, dry_run=False)
        assert _artwork_for(db_session, asset_id)[0].is_primary is True

    def test_the_accessory_file_is_left_in_place(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """`cover.*` is a shared contract with the producers, which check for one
        before fetching; removing it would make them re-download every image."""
        asset_id = make_asset()
        cover = _place_cover(accessory_root, asset_id, JPEG)
        run(db_session, store, accessory_root, poster_kind_id, dry_run=False)
        assert cover.read_bytes() == JPEG

    def test_assets_without_covers_are_counted_not_registered(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        with_cover = make_asset()
        make_asset()
        make_asset()
        _place_cover(accessory_root, with_cover, JPEG)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert summary.assets_scanned == 3
        assert summary.covers_found == 1
        assert summary.registered == 1
        assert summary.no_cover == 2

    def test_one_file_shared_by_two_assets_is_stored_once(
        self, db_session, store, accessory_root, artwork_root, poster_kind_id, make_asset
    ):
        a, b = make_asset(), make_asset()
        _place_cover(accessory_root, a, JPEG)
        _place_cover(accessory_root, b, JPEG)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert summary.registered == 2
        assert len(list(artwork_root.rglob("*.jpg"))) == 1
        assert (
            _artwork_for(db_session, a)[0].storage_path
            == _artwork_for(db_session, b)[0].storage_path
        )

    def test_the_stored_path_is_the_digest_of_the_cover(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        asset_id = make_asset()
        _place_cover(accessory_root, asset_id, PNG, ".png")
        run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        digest = hashlib.sha256(PNG).hexdigest()
        assert _artwork_for(db_session, asset_id)[0].storage_path == (
            f"{digest[:2]}/{digest[2:4]}/{digest}.png"
        )


@pytest.mark.integration
class TestIdempotency:

    def test_a_second_run_registers_nothing_new(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        asset_id = make_asset()
        _place_cover(accessory_root, asset_id, JPEG)

        first = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)
        second = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert first.registered == 1
        assert second.registered == 0
        assert second.already_registered == 1
        assert len(_artwork_for(db_session, asset_id)) == 1

    def test_a_second_run_does_not_reread_covered_assets(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """The already-registered check happens before the file is looked for, so a
        re-run over the whole catalogue costs one query rather than 13,329."""
        asset_id = make_asset()
        cover = _place_cover(accessory_root, asset_id, JPEG)
        run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        cover.unlink()  # the pass must not need it a second time
        second = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert second.already_registered == 1
        assert second.covers_found == 0
        assert second.failed == 0


@pytest.mark.integration
class TestResilience:

    def test_one_bad_file_does_not_end_the_pass(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """A pass over thousands must not be ended by one corrupt image."""
        bad = make_asset()
        good = make_asset()
        _place_cover(accessory_root, bad, b"<!doctype html>" + b" " * 32)
        _place_cover(accessory_root, good, JPEG)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert summary.registered == 1
        assert summary.skipped == {"not a supported image": 1}
        assert _artwork_for(db_session, good) != []
        assert _artwork_for(db_session, bad) == []

    def test_an_oversized_cover_is_skipped_with_its_own_reason(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        _place_cover(accessory_root, make_asset(), JPEG + b"\x00" * (MAX_ARTWORK_BYTES + 1))
        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)
        assert summary.skipped == {"over the size cap": 1}

    def test_an_empty_cover_is_skipped_with_its_own_reason(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """Distinct causes get distinct buckets, or the summary cannot tell a corrupt
        library from a truncated download."""
        _place_cover(accessory_root, make_asset(), b"")
        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)
        assert summary.skipped == {"empty file": 1}

    def test_a_failed_insert_does_not_poison_the_rest_of_the_pass(
        self, db_session, store, accessory_root, poster_kind_id, make_asset, monkeypatch
    ):
        """Counting a failure and carrying on is only real if the session survives it.

        A failed flush leaves the session needing a rollback, so without one the *next*
        asset raises PendingRollbackError instead of being registered -- and the real
        cause is reported once, against the wrong asset, for the whole rest of the run.
        """
        first = make_asset()
        second = make_asset()
        _place_cover(accessory_root, first, JPEG)
        _place_cover(accessory_root, second, PNG, ".png")

        real_insert = backfill._insert
        calls = {"n": 0}

        def failing_once(session, asset_id, kind_id, stored):
            calls["n"] += 1
            if calls["n"] == 1:
                # A statement that genuinely fails, rather than a bare `raise`: the bug
                # is the session's state after a failed flush, which only a real
                # database error produces.
                session.execute(sql_text("SELECT * FROM a_table_that_does_not_exist"))
            return real_insert(session, asset_id, kind_id, stored)

        monkeypatch.setattr(backfill, "_insert", failing_once)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert summary.failed == 1
        assert summary.registered == 1
        assert _artwork_for(db_session, first) == []
        assert _artwork_for(db_session, second) != []

    def test_the_pass_does_not_stop_early_on_a_run_of_covered_assets(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """Covered and uncovered assets are interleaved arbitrarily, so a stretch of
        already-registered ones says nothing about what comes after."""
        covered = [make_asset() for _ in range(3)]
        for asset_id in covered:
            _place_cover(accessory_root, asset_id, JPEG)
        run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        late = make_asset()
        _place_cover(accessory_root, late, PNG, ".png")

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert summary.already_registered == 3
        assert summary.registered == 1
        assert _artwork_for(db_session, late) != []


@pytest.mark.integration
class TestLimit:

    def test_limit_stops_the_pass_and_says_so(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        """A truncated pass that reported a clean summary would read as complete
        coverage when it is not."""
        for i in range(3):
            _place_cover(accessory_root, make_asset(), JPEG + bytes([i]))

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False, limit=2)

        assert summary.registered == 2
        assert summary.limit_reached is True

    def test_a_complete_pass_does_not_claim_it_was_truncated(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        _place_cover(accessory_root, make_asset(), JPEG)
        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False, limit=5)
        assert summary.limit_reached is False

    def test_a_limited_run_can_be_resumed(
        self, db_session, store, accessory_root, poster_kind_id, make_asset
    ):
        for i in range(3):
            _place_cover(accessory_root, make_asset(), JPEG + bytes([i]))

        run(db_session, store, accessory_root, poster_kind_id, dry_run=False, limit=2)
        second = run(db_session, store, accessory_root, poster_kind_id, dry_run=False)

        assert second.registered == 1
        assert second.already_registered == 2

    def test_the_limit_is_honoured_when_every_registration_fails(
        self, db_session, store, accessory_root, poster_kind_id, make_asset, monkeypatch
    ):
        """The bound has to hold on the run that needs bounding.

        `--limit 1` against a misconfigured database walked 500 assets, because the
        limit was spent by successful registrations and there were none. A failing
        write must consume the allowance exactly as a successful one does.
        """
        for i in range(4):
            _place_cover(accessory_root, make_asset(), JPEG + bytes([i]))

        def always_fails(session, asset_id, kind_id, stored):
            session.execute(sql_text("SELECT * FROM a_table_that_does_not_exist"))

        monkeypatch.setattr(backfill, "_insert", always_fails)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False, limit=2)

        assert summary.registered == 0
        assert summary.failed == 2
        assert summary.limit_reached is True

    def test_an_already_present_row_still_spends_the_limit(
        self, db_session, store, accessory_root, poster_kind_id, make_asset, monkeypatch
    ):
        """A racing writer must not buy the pass an unbounded number of extra assets."""
        for i in range(4):
            _place_cover(accessory_root, make_asset(), JPEG + bytes([i]))

        def always_conflicts(session, asset_id, kind_id, stored):
            raise UniqueViolation("artwork already exists")

        monkeypatch.setattr(backfill, "_insert", always_conflicts)

        summary = run(db_session, store, accessory_root, poster_kind_id, dry_run=False, limit=2)

        assert summary.already_registered == 2
        assert summary.limit_reached is True
