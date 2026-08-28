"""Integration tests for a title's resolved poster (issue #105).

The rule: a title uses its own primary artwork; failing that it borrows from the first
entry of its contents, in `order_key` order, recursing into child titles. A title with
nothing beneath it resolves to nothing.

Two things get asserted hardest here, because they are the ones a correctness test
cannot see: that resolution costs the same number of queries whatever the page size,
and that a containment cycle does not hang the walk.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.repositories import (
    SQLAlchemyArtworkRepository,
    SQLAlchemyMediaRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleRepository,
)
from app.schemas import (
    ArtworkCreateInternal,
    AssetCreateInternal,
    TitleContentCreateInternal,
    TitleCreateInternal,
)
from app.schemas.enums import ContentKind, MembershipKind, EntityTypeEnum


@contextmanager
def _statements_touching(engine: Engine, table: str) -> Iterator[list[str]]:
    """Collect every SQL statement issued against ``table`` inside the block."""
    seen: list[str] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if table in statement.lower():
            seen.append(statement)

    event.listen(engine, "after_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine, "after_cursor_execute", _record)


@pytest.fixture
def world(db_session: Session, title_type_ids: dict[str, int], artwork_kind_ids: dict[str, int]):
    """Helpers for building titles, assets, containment and artwork."""
    titles = SQLAlchemyTitleRepository(db_session)
    assets = SQLAlchemyMediaRepository(db_session)
    contents = SQLAlchemyTitleContentRepository(db_session)
    artwork = SQLAlchemyArtworkRepository(db_session)
    counter = {"n": 0}
    keys: dict[int, int] = {}

    class World:
        poster_kind = artwork_kind_ids["poster"]
        backdrop_kind = artwork_kind_ids["backdrop"]

        def title(self, name: str = "T", code: str = "movie") -> int:
            return titles.create(
                TitleCreateInternal(name=name, title_type_id=title_type_ids[code])
            ).id

        def asset(self) -> int:
            counter["n"] += 1
            n = counter["n"]
            return assets.create(
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

        def _next_key(self, parent: int) -> str:
            """A distinct order_key per entry, since uq_parent_order forbids reuse."""
            keys.setdefault(parent, 0)
            keys[parent] += 1
            return f"m{keys[parent]:03d}"

        def contains_title(
            self,
            parent: int,
            child: int,
            order_key: str | None = None,
            membership: MembershipKind = MembershipKind.intrinsic,
        ) -> None:
            contents.create(
                TitleContentCreateInternal(
                    parent_title_id=parent,
                    kind=ContentKind.title,
                    child_title_id=child,
                    asset_id=None,
                    label=None,
                    membership=membership,
                    order_key=order_key or self._next_key(parent),
                )
            )

        def contains_asset(self, parent: int, asset_id: int, order_key: str | None = None) -> None:
            contents.create(
                TitleContentCreateInternal(
                    parent_title_id=parent,
                    kind=ContentKind.asset,
                    child_title_id=None,
                    asset_id=asset_id,
                    label=None,
                    order_key=order_key or self._next_key(parent),
                )
            )

        def art(
            self,
            entity_type: EntityTypeEnum,
            entity_id: int,
            *,
            kind_id: int | None = None,
            path: str | None = None,
            primary: bool = True,
        ) -> int:
            counter["n"] += 1
            digest = f"{counter['n']:064x}"
            return artwork.create(
                ArtworkCreateInternal(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    artwork_kind_id=kind_id or self.poster_kind,
                    storage_path=path or f"{digest[:2]}/{digest[2:4]}/{digest}.jpg",
                    mime="image/jpeg",
                    width=None,
                    height=None,
                    is_primary=primary,
                    source_scheme_id=None,
                    source_external_id=None,
                    source_url=None,
                )
            ).id

    return World()


def _poster(client: TestClient, title_id: int) -> dict | None:
    response = client.get(f"/api/titles/{title_id}")
    assert response.status_code == HTTPStatus.OK, response.text
    return response.json()["poster"]


@pytest.mark.api
@pytest.mark.integration
class TestOwnArtworkWins:

    def test_a_title_uses_its_own_poster(self, client, world):
        title = world.title()
        artwork_id = world.art(EntityTypeEnum.title, title)
        assert _poster(client, title)["id"] == artwork_id

    def test_its_own_beats_a_childs(self, client, world):
        """Depth 0 sorts first, so a season with a poster does not borrow one."""
        season = world.title("Season", "season")
        episode = world.title("Episode", "tv")
        world.contains_title(season, episode)
        world.art(EntityTypeEnum.title, episode)
        own = world.art(EntityTypeEnum.title, season)

        assert _poster(client, season)["id"] == own

    def test_a_non_primary_artwork_is_not_resolved(self, client, world):
        """Only the primary is the one to show; the rest are alternatives."""
        title = world.title()
        world.art(EntityTypeEnum.title, title, primary=False)
        assert _poster(client, title) is None

    def test_another_kind_is_not_resolved_as_a_poster(self, client, world):
        title = world.title()
        world.art(EntityTypeEnum.title, title, kind_id=world.backdrop_kind)
        assert _poster(client, title) is None


@pytest.mark.api
@pytest.mark.integration
class TestFallback:

    def test_a_title_borrows_from_its_child_title(self, client, world):
        season = world.title("Season", "season")
        episode = world.title("Episode", "tv")
        world.contains_title(season, episode)
        borrowed = world.art(EntityTypeEnum.title, episode)

        assert _poster(client, season)["id"] == borrowed

    def test_a_title_borrows_from_its_asset(self, client, world):
        """The case the #104 backfill created thousands of: artwork sits on assets."""
        title = world.title()
        asset = world.asset()
        world.contains_asset(title, asset)
        borrowed = world.art(EntityTypeEnum.asset, asset)

        resolved = _poster(client, title)
        assert resolved["id"] == borrowed
        assert resolved["entity_type"] == "asset"

    def test_it_walks_more_than_one_level(self, client, world):
        """Collection -> season -> episode -> asset is four levels in real data."""
        collection = world.title("Collection", "collection")
        season = world.title("Season", "season")
        episode = world.title("Episode", "tv")
        asset = world.asset()
        world.contains_title(collection, season)
        world.contains_title(season, episode)
        world.contains_asset(episode, asset)
        deep = world.art(EntityTypeEnum.asset, asset)

        assert _poster(client, collection)["id"] == deep

    def test_the_nearest_artwork_wins(self, client, world):
        """A closer relative is a better guess than a distant one."""
        collection = world.title("Collection", "collection")
        season = world.title("Season", "season")
        episode = world.title("Episode", "tv")
        world.contains_title(collection, season)
        world.contains_title(season, episode)
        world.art(EntityTypeEnum.title, episode)
        nearer = world.art(EntityTypeEnum.title, season)

        assert _poster(client, collection)["id"] == nearer

    def test_order_key_decides_between_siblings(self, client, world):
        """ "The first entry of its contents" has to mean the list's own order, which
        is order_key -- not insertion order and not id order."""
        season = world.title("Season", "season")
        second = world.title("Second", "tv")
        first = world.title("First", "tv")
        world.contains_title(season, second, order_key="z")
        world.contains_title(season, first, order_key="a")
        world.art(EntityTypeEnum.title, second)
        expected = world.art(EntityTypeEnum.title, first)

        assert _poster(client, season)["id"] == expected

    def test_a_title_with_nothing_beneath_it_resolves_to_nothing(self, client, world):
        """The grid's placeholder case, not an error."""
        assert _poster(client, world.title()) is None

    def test_contents_without_artwork_resolve_to_nothing(self, client, world):
        season = world.title("Season", "season")
        world.contains_title(season, world.title("Episode", "tv"))
        world.contains_asset(season, world.asset())
        assert _poster(client, season) is None

    def test_a_sibling_branch_is_searched_when_the_first_has_none(self, client, world):
        """Ordering picks the first entry, but an entry with no artwork must not stop
        the search -- the second branch still gets looked at."""
        season = world.title("Season", "season")
        empty = world.title("Empty", "tv")
        stocked = world.title("Stocked", "tv")
        world.contains_title(season, empty, order_key="a")
        world.contains_title(season, stocked, order_key="b")
        expected = world.art(EntityTypeEnum.title, stocked)

        assert _poster(client, season)["id"] == expected


@pytest.mark.api
@pytest.mark.integration
class TestGraphSafety:
    """The walk is over a DAG, and #88 leaves cycles unprevented."""

    def test_a_cycle_does_not_hang_the_walk(self, client, world):
        """Two titles containing each other. Without the visited-set guard in the
        recursive term this recurses until the depth cap on every request; without a
        cap at all it never terminates."""
        a = world.title("A", "season")
        b = world.title("B", "season")
        world.contains_title(a, b)
        world.contains_title(b, a)
        expected = world.art(EntityTypeEnum.title, b)

        assert _poster(client, a)["id"] == expected

    def test_a_cycle_with_no_artwork_anywhere_still_terminates(self, client, world):
        a = world.title("A", "season")
        b = world.title("B", "season")
        world.contains_title(a, b)
        world.contains_title(b, a)
        assert _poster(client, a) is None

    def test_a_shared_child_resolves_for_both_parents(self, client, world):
        """A DAG, not a tree: one episode may sit under a season and a collection.

        Which is the intrinsic/curated split #90 went on to name: the season is the
        episode's home, the collection merely lists it. Resolution deliberately does not
        care -- a curated list showing a poster drawn from what it lists is right, and
        the membership rule that counts intrinsic edges only belongs to aggregates
        (#96), not to artwork.
        """
        season = world.title("Season", "season")
        collection = world.title("Collection", "collection")
        episode = world.title("Episode", "tv")
        world.contains_title(season, episode)
        world.contains_title(collection, episode, membership=MembershipKind.curated)
        shared = world.art(EntityTypeEnum.title, episode)

        assert _poster(client, season)["id"] == shared
        assert _poster(client, collection)["id"] == shared

    def test_a_chain_deeper_than_the_cap_resolves_to_nothing(self, client, world):
        """The cap is a real limit, not decoration. Artwork below it is not found,
        which is the deliberate trade for a bounded read."""
        from app.repositories.artwork_repository import MAX_RESOLUTION_DEPTH

        chain = [world.title(f"L{i}", "season") for i in range(MAX_RESOLUTION_DEPTH + 2)]
        for parent, child in zip(chain, chain[1:], strict=False):
            world.contains_title(parent, child)
        world.art(EntityTypeEnum.title, chain[-1])

        assert _poster(client, chain[0]) is None


@pytest.mark.api
@pytest.mark.integration
class TestListEndpoint:

    def test_the_list_omits_the_poster_unless_asked(self, client, world):
        title = world.title()
        world.art(EntityTypeEnum.title, title)

        items = client.get("/api/titles/").json()["items"]
        assert all(item["poster"] is None for item in items)

    def test_include_poster_populates_it(self, client, world):
        title = world.title()
        artwork_id = world.art(EntityTypeEnum.title, title)

        items = client.get("/api/titles/?include=poster").json()["items"]
        assert [i["poster"]["id"] for i in items if i["id"] == title] == [artwork_id]

    def test_include_poster_combines_with_other_inclusions(self, client, world):
        title = world.title()
        world.art(EntityTypeEnum.title, title)

        items = client.get("/api/titles/?include=tags,poster").json()["items"]
        row = next(i for i in items if i["id"] == title)
        assert row["poster"] is not None
        assert row["tags"] == []

    def test_the_list_resolves_the_same_way_the_detail_does(self, client, world):
        season = world.title("Season", "season")
        episode = world.title("Episode", "tv")
        world.contains_title(season, episode)
        world.art(EntityTypeEnum.title, episode)

        items = client.get("/api/titles/?include=poster").json()["items"]
        listed = next(i for i in items if i["id"] == season)["poster"]
        assert listed == _poster(client, season)

    def test_titles_without_a_poster_are_null_not_missing(self, client, world):
        world.title()
        items = client.get("/api/titles/?include=poster").json()["items"]
        assert all("poster" in item for item in items)


@pytest.mark.api
@pytest.mark.integration
class TestTitleListQueryCount:
    """Guard the cost of resolution against the N+1 regression #49 recorded.

    A resolution walk evaluated per row is invisible to every correctness test above --
    the response body is identical either way -- so it is asserted as a query count.
    #49 measured that shape at 14.6s against 263ms at the 500-row cap.
    """

    def test_resolution_does_not_scale_with_rows_returned(self, client, world, _test_engine):
        for _ in range(6):
            title = world.title()
            world.art(EntityTypeEnum.title, title)

        counts: dict[int, int] = {}
        for limit in (2, 6):
            with _statements_touching(_test_engine, "artwork") as statements:
                response = client.get(f"/api/titles/?include=poster&limit={limit}")
            assert response.status_code == HTTPStatus.OK
            items = response.json()["items"]
            assert len(items) == limit
            # The field must actually be populated, or the count proves nothing.
            assert all(item["poster"] for item in items)
            counts[limit] = len(statements)

        assert counts[2] == counts[6], (
            f"artwork queries scaled with page size: "
            f"{counts[2]} for 2 rows, {counts[6]} for 6 rows"
        )
        assert counts[6] <= 2, f"expected one resolution query per page, got {counts[6]}"

    def test_a_deep_hierarchy_still_costs_one_query_per_page(self, client, world, _test_engine):
        """Depth is handled inside the recursive CTE, so it must not add queries."""
        for _ in range(4):
            season = world.title("Season", "season")
            episode = world.title("Episode", "tv")
            asset = world.asset()
            world.contains_title(season, episode)
            world.contains_asset(episode, asset)
            world.art(EntityTypeEnum.asset, asset)

        with _statements_touching(_test_engine, "artwork") as statements:
            response = client.get("/api/titles/?include=poster&limit=12")
        assert response.status_code == HTTPStatus.OK
        assert len(statements) <= 2, f"expected one resolution query, got {len(statements)}"

    def test_asking_without_include_issues_no_artwork_query_at_all(
        self, client, world, _test_engine
    ):
        world.art(EntityTypeEnum.title, world.title())

        with _statements_touching(_test_engine, "artwork_walk") as statements:
            client.get("/api/titles/")
        assert statements == []
