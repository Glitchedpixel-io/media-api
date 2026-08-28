"""Integration tests for the has_artwork filter on the two listings (issue #114).

"Which assets are still missing a cover?" had no query behind it: the only way to find
out was to page the whole collection and inspect every row. What matters here is that
both directions of the filter agree on the same set, that an entity holding several
artworks is still returned once, and that a title's own artwork is not confused with
the poster it may resolve from its contents.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.repositories import SQLAlchemyArtworkRepository, SQLAlchemyMediaRepository
from app.repositories.protocols import TitleRepository
from app.schemas import ArtworkCreateInternal, AssetCreateInternal, TitleCreateInternal
from app.schemas.enums import EntityTypeEnum


@pytest.fixture
def make_asset(db_session):
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


@pytest.fixture
def give_artwork(db_session, artwork_kind_ids: dict[str, int]):
    repo = SQLAlchemyArtworkRepository(db_session)
    counter = {"n": 0}

    def _give(
        entity_type: EntityTypeEnum, entity_id: int, kind: str = "poster", is_primary: bool = True
    ) -> int:
        counter["n"] += 1
        n = counter["n"]
        return repo.create(
            ArtworkCreateInternal(
                entity_type=entity_type,
                entity_id=entity_id,
                artwork_kind_id=artwork_kind_ids[kind],
                storage_path=f"ab/cd/{n:064x}.jpg",
                mime="image/jpeg",
                width=None,
                height=None,
                is_primary=is_primary,
                source_scheme_id=None,
                source_external_id=None,
                source_url=None,
            )
        ).id

    return _give


@pytest.mark.integration
class TestAssets:

    def test_true_returns_only_assets_with_artwork(
        self, client: TestClient, make_asset, give_artwork
    ):
        covered = make_asset()
        make_asset()
        give_artwork(EntityTypeEnum.asset, covered)

        body = client.get("/api/assets/", params={"has_artwork": "true"}).json()

        assert [item["id"] for item in body["items"]] == [covered]

    def test_false_returns_only_assets_without_artwork(
        self, client: TestClient, make_asset, give_artwork
    ):
        """The query the backfill wants: what is still missing a cover."""
        covered = make_asset()
        bare = make_asset()
        give_artwork(EntityTypeEnum.asset, covered)

        body = client.get("/api/assets/", params={"has_artwork": "false"}).json()

        assert [item["id"] for item in body["items"]] == [bare]

    def test_the_two_directions_partition_the_collection(
        self, client: TestClient, make_asset, give_artwork
    ):
        """Neither overlapping nor between them losing a row."""
        ids = {make_asset() for _ in range(4)}
        give_artwork(EntityTypeEnum.asset, min(ids))

        with_art = {
            item["id"]
            for item in client.get("/api/assets/", params={"has_artwork": "true"}).json()["items"]
        }
        without = {
            item["id"]
            for item in client.get("/api/assets/", params={"has_artwork": "false"}).json()["items"]
        }

        assert with_art | without == ids
        assert with_art & without == set()

    def test_an_asset_with_several_artworks_is_returned_once(
        self, client: TestClient, make_asset, give_artwork
    ):
        """A join would return it once per artwork and make limit a cap on the wrong
        thing."""
        asset_id = make_asset()
        give_artwork(EntityTypeEnum.asset, asset_id, kind="poster")
        give_artwork(EntityTypeEnum.asset, asset_id, kind="backdrop", is_primary=False)

        body = client.get("/api/assets/", params={"has_artwork": "true"}).json()

        assert [item["id"] for item in body["items"]] == [asset_id]

    def test_omitting_the_filter_returns_everything(
        self, client: TestClient, make_asset, give_artwork
    ):
        covered = make_asset()
        bare = make_asset()
        give_artwork(EntityTypeEnum.asset, covered)

        body = client.get("/api/assets/").json()

        assert {item["id"] for item in body["items"]} == {covered, bare}

    def test_a_titles_artwork_does_not_make_an_asset_look_covered(
        self,
        client: TestClient,
        make_asset,
        give_artwork,
        title_repository: TitleRepository,
        title_type_ids,
    ):
        """entity_id alone is ambiguous across the two entity types, so the filter has
        to pin entity_type or an id collision reads as a cover."""
        asset_id = make_asset()
        title_id = title_repository.create(
            TitleCreateInternal(name="A Title", title_type_id=title_type_ids["movie"])
        ).id
        give_artwork(EntityTypeEnum.title, title_id)

        body = client.get("/api/assets/", params={"has_artwork": "true"}).json()

        assert [item["id"] for item in body["items"]] == []
        assert asset_id is not None


@pytest.mark.integration
class TestTitles:

    @pytest.fixture
    def make_title(self, title_repository: TitleRepository, title_type_ids):
        counter = {"n": 0}

        def _make() -> int:
            counter["n"] += 1
            return title_repository.create(
                TitleCreateInternal(
                    name=f"Title {counter['n']}", title_type_id=title_type_ids["movie"]
                )
            ).id

        return _make

    def test_true_returns_only_titles_with_their_own_artwork(
        self, client: TestClient, make_title, give_artwork
    ):
        covered = make_title()
        make_title()
        give_artwork(EntityTypeEnum.title, covered)

        body = client.get("/api/titles/", params={"has_artwork": "true"}).json()

        assert [item["id"] for item in body["items"]] == [covered]

    def test_false_returns_only_titles_without_their_own_artwork(
        self, client: TestClient, make_title, give_artwork
    ):
        covered = make_title()
        bare = make_title()
        give_artwork(EntityTypeEnum.title, covered)

        body = client.get("/api/titles/", params={"has_artwork": "false"}).json()

        assert [item["id"] for item in body["items"]] == [bare]

    def test_it_reports_the_titles_own_artwork_not_a_resolved_poster(
        self, client: TestClient, make_title, give_artwork
    ):
        """A title with no artwork of its own can still show a poster borrowed from its
        contents. This filter deliberately answers the first question, not the second --
        so a title holding nothing is `has_artwork=false` regardless of what
        `include=poster` would resolve for it."""
        bare = make_title()

        body = client.get("/api/titles/", params={"has_artwork": "false"}).json()

        assert [item["id"] for item in body["items"]] == [bare]

    def test_the_filter_composes_with_name(self, client: TestClient, make_title, give_artwork):
        wanted = make_title()
        other = make_title()
        give_artwork(EntityTypeEnum.title, wanted)
        give_artwork(EntityTypeEnum.title, other)

        body = client.get("/api/titles/", params={"has_artwork": "true", "name": "Title 1"}).json()

        assert [item["id"] for item in body["items"]] == [wanted]
