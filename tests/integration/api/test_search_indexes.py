"""Search and sort index coverage (issue #98).

Two different things are asserted here, and they fail for different reasons.

**Behaviour.** The `filename_ext` filter was rewritten from `filename ILIKE '%.ext'`
to a match on the extension expression so it can use ``ix_assets_filename_ext``. The
two forms are meant to be equivalent and nothing asserted that, so these pin the
behaviour rather than the implementation -- the same approach #60 took when it
rewrote the `path_prefix` filter.

**Index usage.** A functional index only serves a query whose expression parses to
the same tree as the index's. ``ix_assets_filename_ext`` is declared from
``FILENAME_EXTENSION_INDEX_SQL`` (spelled as PostgreSQL stores it, so `alembic check`
passes) while the query is built by ``filename_extension`` (built from ``func``, so
it is properly table-qualified). Those are two spellings of one expression, and
nothing but a test stops them drifting apart into an index that is never used.

The plan assertions run with ``enable_seqscan`` off. Test fixtures hold a handful of
rows, where PostgreSQL is right to sequential-scan whatever indexes exist, so a bare
EXPLAIN would assert nothing. Disabling the sequential scan asks the question that
actually matters -- *can* this query use this index -- rather than whether the
planner currently prefers it.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import AssetORM, TitleORM
from app.models.asset import filename_extension
from app.repositories.protocols import MediaRepository
from app.schemas import AssetCreateInternal
from tests.factories import AssetReadFactory


def _plan_for(db_session: Session, stmt) -> str:
    """The query plan for a statement, with sequential scans disabled.

    Args:
        db_session: The session to plan against.
        stmt: The SQLAlchemy statement to explain.

    Returns:
        str: The whole plan, newline-joined, for a substring assertion.
    """
    compiled = stmt.compile(db_session.get_bind(), compile_kwargs={"literal_binds": True})
    db_session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = db_session.execute(text("EXPLAIN " + str(compiled))).all()
    return "\n".join(r[0] for r in rows)


@pytest.mark.api
@pytest.mark.integration
class TestFilenameExtBehaviour:
    """What `?filename_ext=` matches, independent of how it is indexed."""

    def _seed(self, media_repository: MediaRepository, *names: str) -> None:
        for name in names:
            asset = AssetReadFactory(path=f"media/{name}", filename=Path(name).name)
            media_repository.create(
                AssetCreateInternal(
                    **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
                )
            )

    def _paths(self, client: TestClient, ext: str) -> set[str]:
        res = client.get(f"/api/assets?filename_ext={ext}&limit=50")
        assert res.status_code == HTTPStatus.OK, res.text
        return {item["filename"] for item in res.json()["items"]}

    def test_matches_only_that_extension(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        self._seed(media_repository, "one.mkv", "two.mp4", "three.mkv")

        assert self._paths(client, "mkv") == {"one.mkv", "three.mkv"}

    def test_a_leading_dot_is_accepted(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """`?filename_ext=.mkv` and `?filename_ext=mkv` mean the same thing."""
        self._seed(media_repository, "one.mkv", "two.mp4")

        assert self._paths(client, ".mkv") == self._paths(client, "mkv") == {"one.mkv"}

    def test_the_match_is_case_insensitive(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """ILIKE was case-insensitive, so the replacement has to be too."""
        self._seed(media_repository, "shouty.MKV", "quiet.mkv")

        assert self._paths(client, "mkv") == {"shouty.MKV", "quiet.mkv"}
        assert self._paths(client, "MKV") == {"shouty.MKV", "quiet.mkv"}

    def test_only_the_final_extension_counts(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """`a.mkv.bak` is a .bak file, which is what `ILIKE '%.mkv'` also said."""
        self._seed(media_repository, "archive.mkv.bak", "real.mkv")

        assert self._paths(client, "mkv") == {"real.mkv"}
        assert self._paths(client, "bak") == {"archive.mkv.bak"}

    def test_a_name_with_no_extension_matches_nothing(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """The expression is NULL for these, and NULL matches no extension.

        The trap the regex avoids: a file literally named `mkv` must not answer
        `?filename_ext=mkv`.
        """
        self._seed(media_repository, "README", "mkv", "real.mkv")

        assert self._paths(client, "mkv") == {"real.mkv"}

    def test_a_dotfile_is_its_own_extension(
        self, client: TestClient, media_repository: MediaRepository
    ) -> None:
        """`.mkv` with no stem matched `ILIKE '%.mkv'`, so it still matches."""
        self._seed(media_repository, ".mkv")

        assert self._paths(client, "mkv") == {".mkv"}


@pytest.mark.integration
class TestIndexesAreUsable:
    """Each index must actually serve the query it was added for.

    These are drift guards, not performance tests. An index whose expression no
    longer matches its query is invisible -- the results stay correct and the scan
    comes back -- which is the failure #60 recorded and this PR risks reintroducing.
    """

    def test_filename_ext_filter_uses_the_index(self, db_session: Session) -> None:
        """The two spellings of the extension expression must still agree.

        `FILENAME_EXTENSION_INDEX_SQL` builds the index and `filename_extension`
        builds the query. If either changes alone this is the only thing that fails.
        """
        stmt = select(AssetORM.id).where(filename_extension(AssetORM.filename) == "mkv")

        assert "ix_assets_filename_ext" in _plan_for(db_session, stmt)

    def test_path_part_filter_uses_the_trigram_index(self, db_session: Session) -> None:
        """gin_trgm_ops serves ILIKE directly, with no lower() rewrite."""
        stmt = select(AssetORM.id).where(AssetORM.path.ilike("%severance%"))

        assert "ix_assets_path_trgm" in _plan_for(db_session, stmt)

    def test_name_filter_uses_the_trigram_index(self, db_session: Session) -> None:
        stmt = select(TitleORM.id).where(TitleORM.name.ilike("%arrival%"))

        assert "ix_titles_name_trgm" in _plan_for(db_session, stmt)

    def test_name_sort_uses_the_btree(self, db_session: Session) -> None:
        """The trigram index cannot serve this -- GIN has no order."""
        stmt = select(TitleORM.id).order_by(TitleORM.name, TitleORM.id).limit(50)

        assert "ix_titles_name" in _plan_for(db_session, stmt)

    def test_the_trigram_extension_is_installed_by_create_all(self, db_session: Session) -> None:
        """Tests build their schema from the models, not from migrations.

        Without the before_create hook in app/database.py the whole suite fails at
        schema creation with `operator class "gin_trgm_ops" does not exist`, so this
        asserts the hook rather than leaving it to be rediscovered.
        """
        installed = db_session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
        ).first()

        assert installed is not None
