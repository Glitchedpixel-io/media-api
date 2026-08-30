"""Integration tests for `resolves_display_image` on GET /api/titles/ (issue #122).

`has_artwork` (#114) answers "does this title hold artwork of its own?". For a browse
grid that is the wrong question: since #110 a title with no artwork of its own still
shows one borrowed from its contents, so "shows nothing" -- the hole in the grid -- had
no query behind it. `has_artwork=false` is true of nearly every title in the library
and identifies almost nothing.

The assertion that matters most is not any individual case but
`TestFilterAgreesWithResolution`: the filter's answer and the `include=display_image`
field are produced by two different walks, one descending and one ascending, and a
filter that disagreed with the field it filters on would be worse than no filter --
callers would get titles whose image is null under `=true`, and miss holes under
`=false`.
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
from app.repositories.artwork_repository import MAX_RESOLUTION_DEPTH
from app.schemas import (
    ArtworkCreateInternal,
    AssetCreateInternal,
    TitleContentCreateInternal,
    TitleCreateInternal,
)
from app.schemas.enums import ContentKind, EntityTypeEnum, MembershipKind


@contextmanager
def _statements_touching(engine: Engine, table: str) -> Iterator[list[str]]:
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
    titles = SQLAlchemyTitleRepository(db_session)
    assets = SQLAlchemyMediaRepository(db_session)
    contents = SQLAlchemyTitleContentRepository(db_session)
    artwork = SQLAlchemyArtworkRepository(db_session)
    counter = {"n": 0}
    keys: dict[int, int] = {}

    class World:
        thumbnail_kind = artwork_kind_ids["thumbnail"]
        cover_kind = artwork_kind_ids["cover_art"]
        logo_kind = artwork_kind_ids["logo"]
        banner_kind = artwork_kind_ids["banner"]

        def title(self, name: str = "T", code: str = "movie", root: bool = False) -> int:
            counter["n"] += 1
            return titles.create(
                TitleCreateInternal(
                    name=f"{name} {counter['n']}",
                    title_type_id=title_type_ids[code],
                    library_root=root,
                )
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
            keys.setdefault(parent, 0)
            keys[parent] += 1
            return f"m{keys[parent]:03d}"

        def contains_title(
            self,
            parent: int,
            child: int,
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
                    order_key=self._next_key(parent),
                )
            )

        def contains_asset(self, parent: int, asset_id: int) -> None:
            contents.create(
                TitleContentCreateInternal(
                    parent_title_id=parent,
                    kind=ContentKind.asset,
                    child_title_id=None,
                    asset_id=asset_id,
                    label=None,
                    order_key=self._next_key(parent),
                )
            )

        def art(
            self,
            entity_type: EntityTypeEnum,
            entity_id: int,
            *,
            kind_id: int | None = None,
            primary: bool = True,
        ) -> int:
            counter["n"] += 1
            digest = f"{counter['n']:064x}"
            return artwork.create(
                ArtworkCreateInternal(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    artwork_kind_id=kind_id or self.thumbnail_kind,
                    storage_path=digest,
                    mime="image/jpeg",
                    width=1280,
                    height=720,
                    is_primary=primary,
                    source_scheme_id=None,
                    source_external_id=None,
                    source_url=None,
                )
            ).id

    return World()


def _ids(client: TestClient, query: str) -> set[int]:
    response = client.get(f"/api/titles/?{query}&limit=500")
    assert response.status_code == HTTPStatus.OK, response.text
    return {item["id"] for item in response.json()["items"]}


def _resolving(client: TestClient) -> set[int]:
    """The titles whose `include=display_image` is non-null -- the ground truth."""
    response = client.get("/api/titles/?include=display_image&limit=500")
    assert response.status_code == HTTPStatus.OK, response.text
    return {item["id"] for item in response.json()["items"] if item["display_image"] is not None}


def _all(client: TestClient) -> set[int]:
    return _ids(client, "sort=id:asc")


@pytest.mark.api
@pytest.mark.integration
class TestFilterAgreesWithResolution:
    """The filter and the field must never disagree.

    They are computed by different walks -- the field descends from each title, the
    filter ascends from the artwork -- so this is a real cross-check, not a tautology.
    """

    def test_they_agree_on_a_world_with_every_shape_in_it(self, client, world):
        # own artwork
        own = world.title("Own", root=True)
        world.art(EntityTypeEnum.title, own)
        # borrows from a child title
        season = world.title("Season", "season", root=True)
        episode = world.title("Episode", "episode")
        world.contains_title(season, episode)
        world.art(EntityTypeEnum.title, episode)
        # borrows from an asset, two levels down
        collection = world.title("Collection", "collection", root=True)
        deep_season = world.title("DeepSeason", "season")
        deep_episode = world.title("DeepEpisode", "episode")
        asset = world.asset()
        world.contains_title(collection, deep_season)
        world.contains_title(deep_season, deep_episode)
        world.contains_asset(deep_episode, asset)
        world.art(EntityTypeEnum.asset, asset)
        # a curated list whose member has artwork
        curated = world.title("Curated", "collection", root=True)
        member = world.title("Member")
        world.contains_title(curated, member, membership=MembershipKind.curated)
        world.art(EntityTypeEnum.title, member)
        # nothing at all
        world.title("Barren", root=True)
        # artwork of a kind outside the chain
        logo_only = world.title("LogoOnly", root=True)
        world.art(EntityTypeEnum.title, logo_only, kind_id=world.logo_kind)

        assert _ids(client, "resolves_display_image=true") == _resolving(client)

    def test_the_two_directions_partition_the_library(self, client, world):
        """Every title is in exactly one side. A title missing from both would be a
        walk that lost it; a title in both would be a predicate that is not a negation."""
        a = world.title("A")
        world.art(EntityTypeEnum.title, a)
        b = world.title("B")
        parent = world.title("Parent", "season")
        child = world.title("Child", "episode")
        world.contains_title(parent, child)
        world.art(EntityTypeEnum.title, child)

        yes = _ids(client, "resolves_display_image=true")
        no = _ids(client, "resolves_display_image=false")

        assert yes | no == _all(client)
        assert yes & no == set()
        assert {a, parent, child} <= yes
        assert b in no

    def test_omitting_the_filter_returns_everything(self, client, world):
        world.art(EntityTypeEnum.title, world.title("A"))
        world.title("B")

        assert _ids(client, "sort=id:asc") == _all(client)


@pytest.mark.api
@pytest.mark.integration
class TestWhatCountsAsResolving:

    def test_a_title_with_its_own_artwork_resolves(self, client, world):
        t = world.title()
        world.art(EntityTypeEnum.title, t)

        assert _ids(client, "resolves_display_image=true") == {t}

    def test_a_title_borrowing_from_a_child_resolves(self, client, world):
        season = world.title("Season", "season")
        episode = world.title("Episode", "episode")
        world.contains_title(season, episode)
        world.art(EntityTypeEnum.title, episode)

        assert _ids(client, "resolves_display_image=true") == {season, episode}

    def test_a_title_borrowing_from_an_asset_resolves(self, client, world):
        """The case the #104 backfill created thousands of: artwork sits on assets."""
        title = world.title()
        asset = world.asset()
        world.contains_asset(title, asset)
        world.art(EntityTypeEnum.asset, asset)

        assert _ids(client, "resolves_display_image=true") == {title}

    def test_a_title_with_nothing_beneath_it_does_not_resolve(self, client, world):
        t = world.title()

        assert _ids(client, "resolves_display_image=false") == {t}

    def test_contents_without_artwork_do_not_resolve(self, client, world):
        season = world.title("Season", "season")
        episode = world.title("Episode", "episode")
        world.contains_title(season, episode)
        world.contains_asset(season, world.asset())

        assert _ids(client, "resolves_display_image=true") == set()

    def test_a_non_primary_artwork_does_not_count(self, client, world):
        """The resolver only ever takes the primary, so the filter must not count a
        row it would never return."""
        t = world.title()
        world.art(EntityTypeEnum.title, t, primary=False)

        assert _ids(client, "resolves_display_image=false") == {t}

    @pytest.mark.parametrize("kind", ["logo", "banner"])
    def test_a_kind_outside_the_display_chain_does_not_count(self, client, world, kind, request):
        """`logo` and `banner` are deliberately outside DISPLAY_IMAGE_KINDS -- neither
        is a thing to show in a grid slot -- so neither may satisfy this filter."""
        kind_id = request.getfixturevalue("artwork_kind_ids")[kind]
        t = world.title()
        world.art(EntityTypeEnum.title, t, kind_id=kind_id)

        assert _ids(client, "resolves_display_image=false") == {t}

    def test_any_kind_in_the_chain_counts(self, client, world):
        """The filter asks whether *something* resolves, so it must not privilege the
        head of the chain -- a cover_art with no poster still fills the grid slot."""
        t = world.title()
        world.art(EntityTypeEnum.title, t, kind_id=world.cover_kind)

        assert _ids(client, "resolves_display_image=true") == {t}

    def test_artwork_deeper_than_the_cap_does_not_resolve(self, client, world):
        """The filter has to apply the same depth cap the resolver does, or it would
        promise an image the grid then renders as a placeholder."""
        chain = [world.title(f"L{i}", "collection") for i in range(MAX_RESOLUTION_DEPTH + 2)]
        for parent, child in zip(chain, chain[1:]):
            world.contains_title(parent, child)
        world.art(EntityTypeEnum.title, chain[-1])

        assert chain[0] in _ids(client, "resolves_display_image=false")


@pytest.mark.api
@pytest.mark.integration
class TestCuratedContainment:
    """The filter inherits #161's rule, because it shares the edge predicate."""

    def test_a_curated_list_does_not_resolve_from_its_members(self, client, world):
        curated = world.title("Films of 1974", "collection")
        member = world.title("Member")
        world.contains_title(curated, member, membership=MembershipKind.curated)
        world.art(EntityTypeEnum.title, member)

        assert curated in _ids(client, "resolves_display_image=false")
        assert member in _ids(client, "resolves_display_image=true")

    def test_a_curated_list_with_its_own_artwork_resolves(self, client, world):
        curated = world.title("Films of 1974", "collection")
        world.contains_title(curated, world.title("Member"), membership=MembershipKind.curated)
        world.art(EntityTypeEnum.title, curated)

        assert curated in _ids(client, "resolves_display_image=true")

    def test_the_filter_finds_exactly_the_grid_holes(self, client, world):
        """The question #122 was opened to make askable: which library entries render
        a placeholder?"""
        stocked = world.title("Stocked", "season", root=True)
        episode = world.title("Episode", "episode")
        world.contains_title(stocked, episode)
        world.art(EntityTypeEnum.asset, world.asset())  # unrelated, unreachable
        world.art(EntityTypeEnum.title, episode)
        hole = world.title("Hole", "collection", root=True)
        world.contains_title(hole, world.title("Listed"), membership=MembershipKind.curated)

        assert _ids(client, "library_root=true&resolves_display_image=false") == {hole}


@pytest.mark.api
@pytest.mark.integration
class TestComposition:

    def test_it_composes_with_library_root_and_type(self, client, world):
        wanted = world.title("Wanted", "movie", root=True)
        world.art(EntityTypeEnum.title, wanted)
        world.title("RootNoArt", "movie", root=True)
        non_root = world.title("NonRoot", "movie")
        world.art(EntityTypeEnum.title, non_root)
        wrong_type = world.title("WrongType", "season", root=True)
        world.art(EntityTypeEnum.title, wrong_type)

        found = _ids(client, "library_root=true&title_type=movie&resolves_display_image=true")

        assert found == {wanted}

    def test_a_title_is_returned_once(self, client, world):
        """The walk reaches a title by every path it has; the semi-join must still
        yield one row, or `limit` stops being a cap on titles."""
        season = world.title("Season", "season")
        episode = world.title("Episode", "episode")
        world.contains_title(season, episode)
        world.art(EntityTypeEnum.title, episode)
        world.art(EntityTypeEnum.title, season, kind_id=world.cover_kind)

        items = client.get("/api/titles/?resolves_display_image=true&limit=500").json()["items"]

        assert len(items) == len({item["id"] for item in items})


@pytest.mark.api
@pytest.mark.integration
class TestFilterQueryCost:

    def test_the_filter_is_one_query_regardless_of_page_size(self, client, world, _test_engine):
        """#122's constraint, and the reason the walk ascends rather than descends: the
        filter must not become one walk per candidate row, which is #49 (14.6s at the
        500-row cap against 263ms)."""
        for _ in range(30):
            season = world.title("Season", "season", root=True)
            episode = world.title("Episode", "episode")
            world.contains_title(season, episode)
            world.contains_asset(episode, world.asset())
            world.art(EntityTypeEnum.title, episode)

        with _statements_touching(_test_engine, "title_contents") as small:
            client.get("/api/titles/?resolves_display_image=true&limit=5")
        with _statements_touching(_test_engine, "title_contents") as large:
            client.get("/api/titles/?resolves_display_image=true&limit=500")

        assert len(small) == len(large)

    def test_an_unfiltered_page_does_not_pay_for_the_kind_lookup(self, client, world, _test_engine):
        """The chain is only resolved to ids when the filter asks for it, so an
        ordinary page is unchanged by this feature existing."""
        world.art(EntityTypeEnum.title, world.title())

        with _statements_touching(_test_engine, "artwork_kinds") as seen:
            client.get("/api/titles/?limit=10")

        assert seen == []
