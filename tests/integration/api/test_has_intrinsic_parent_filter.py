"""Integration tests for `has_intrinsic_parent` on assets and titles (issue #177).

The redesigned front end turns loose material into structure, and "what have I not
placed yet?" is the queue that whole workflow starts from. Neither list endpoint could
express it: `GET /api/assets/` had no containment filter at all, and on
`GET /api/titles/` the `membership` filter matches an *edge*, so it could ask which
titles have a home and nothing could ask which have none.

The load-bearing decision, and the one these tests pin, is that **only intrinsic
containment counts**. An asset dropped into a curated collection has been listed, not
placed -- curated membership is unlimited by design, so counting it would report an
asset that lives nowhere as placed the moment anyone put it in a list. Intrinsic-only
also keeps this agreeing with every other traversal in the API: breadcrumbs,
`TitleMediaTotals`, and display-image borrowing all follow intrinsic edges alone.

`TestTheTwoDirectionsPartition` is the assertion to keep. `false` compiles to a negated
correlated EXISTS, and the two sides failing to sum to the whole collection is what
would catch a predicate that stopped being a true negation -- the same guard
`test_the_two_directions_partition_the_library` provides for `resolves_display_image`.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.repositories import (
    SQLAlchemyMediaRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleRepository,
)
from app.schemas import (
    AssetCreateInternal,
    TitleContentCreateInternal,
    TitleCreateInternal,
)
from app.schemas.enums import ContentKind, MembershipKind


@pytest.fixture
def world(db_session: Session, title_type_ids: dict[str, int]):
    titles = SQLAlchemyTitleRepository(db_session)
    assets = SQLAlchemyMediaRepository(db_session)
    contents = SQLAlchemyTitleContentRepository(db_session)
    counter = {"n": 0}
    positions: dict[int, int] = {}

    class World:
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

        def _next_position(self, parent: int) -> int:
            positions.setdefault(parent, -1)
            positions[parent] += 1
            return positions[parent]

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
                    position=self._next_position(parent),
                )
            )

        def contains_asset(
            self,
            parent: int,
            asset_id: int,
            membership: MembershipKind = MembershipKind.intrinsic,
        ) -> None:
            contents.create(
                TitleContentCreateInternal(
                    parent_title_id=parent,
                    kind=ContentKind.asset,
                    child_title_id=None,
                    asset_id=asset_id,
                    label=None,
                    membership=membership,
                    position=self._next_position(parent),
                )
            )

    return World()


def _ids(client: TestClient, resource: str, query: str) -> set[int]:
    """Every id the filtered collection returns, paged to exhaustion."""
    seen: set[int] = set()
    url = f"/api/{resource}/?{query}&limit=100"
    while url:
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK, response.text
        body = response.json()
        seen.update(item["id"] for item in body["items"])
        cursor = body["page"].get("next")
        url = f"/api/{resource}/?{query}&limit=100&after={cursor}" if cursor else ""
    return seen


def _all(client: TestClient, resource: str) -> set[int]:
    return _ids(client, resource, "sort=id:asc")


@pytest.mark.api
@pytest.mark.integration
class TestAssetsUnplacedQueue:
    """`GET /api/assets/?has_intrinsic_parent=false` -- the drag source."""

    def test_an_asset_with_no_edges_at_all_is_unplaced(self, client, world):
        loose = world.asset()

        assert loose in _ids(client, "assets", "has_intrinsic_parent=false")
        assert loose not in _ids(client, "assets", "has_intrinsic_parent=true")

    def test_an_asset_with_an_intrinsic_home_is_placed(self, client, world):
        placed = world.asset()
        world.contains_asset(world.title("Home"), placed)

        assert placed in _ids(client, "assets", "has_intrinsic_parent=true")
        assert placed not in _ids(client, "assets", "has_intrinsic_parent=false")

    def test_an_asset_only_in_a_curated_list_is_still_unplaced(self, client, world):
        """The decision this filter turns on: listed is not placed."""
        listed = world.asset()
        world.contains_asset(world.title("Best Of"), listed, membership=MembershipKind.curated)

        assert listed in _ids(client, "assets", "has_intrinsic_parent=false")
        assert listed not in _ids(client, "assets", "has_intrinsic_parent=true")

    def test_an_asset_with_a_home_and_curated_listings_is_placed_once(self, client, world):
        """Several edges must not return the asset several times, or `limit` stops
        being a cap on assets and the keyset cursor pages over a multiplied set."""
        asset = world.asset()
        world.contains_asset(world.title("Home"), asset)
        for name in ("List A", "List B", "List C"):
            world.contains_asset(world.title(name), asset, membership=MembershipKind.curated)

        response = client.get("/api/assets/?has_intrinsic_parent=true&limit=100")
        assert response.status_code == HTTPStatus.OK
        returned = [item["id"] for item in response.json()["items"]]
        assert returned.count(asset) == 1

    def test_an_asset_under_two_homes_is_placed_once(self, client, world):
        """Two intrinsic parents is legal for an asset -- the same file under two cuts
        -- unlike a title, which `uq_one_intrinsic_parent` limits to one."""
        asset = world.asset()
        world.contains_asset(world.title("Cut A"), asset)
        world.contains_asset(world.title("Cut B"), asset)

        response = client.get("/api/assets/?has_intrinsic_parent=true&limit=100")
        assert response.status_code == HTTPStatus.OK
        returned = [item["id"] for item in response.json()["items"]]
        assert returned.count(asset) == 1

    def test_omitting_the_filter_returns_everything(self, client, world):
        world.contains_asset(world.title("Home"), world.asset())
        world.asset()

        assert _ids(client, "assets", "sort=id:asc") == _all(client, "assets")


@pytest.mark.api
@pytest.mark.integration
class TestTitlesUnparentedQueue:
    """`GET /api/titles/?has_intrinsic_parent=false&library_root=false`."""

    def test_a_title_with_no_parent_has_no_intrinsic_parent(self, client, world):
        orphan = world.title("Orphan")

        assert orphan in _ids(client, "titles", "has_intrinsic_parent=false")
        assert orphan not in _ids(client, "titles", "has_intrinsic_parent=true")

    def test_a_title_with_a_home_is_parented(self, client, world):
        child = world.title("Episode", "episode")
        world.contains_title(world.title("Season", "season"), child)

        assert child in _ids(client, "titles", "has_intrinsic_parent=true")
        assert child not in _ids(client, "titles", "has_intrinsic_parent=false")

    def test_a_title_only_in_a_curated_list_is_still_unparented(self, client, world):
        listed = world.title("Listed")
        world.contains_title(world.title("Collection"), listed, membership=MembershipKind.curated)

        assert listed in _ids(client, "titles", "has_intrinsic_parent=false")
        assert listed not in _ids(client, "titles", "has_intrinsic_parent=true")

    def test_the_unparented_queue_excludes_library_roots(self, client, world):
        """The DoD's actual query. Rootness is stored, not derived from having a parent
        (#91), so a root with no parent is deliberate rather than unplaced -- and this
        filter does not bundle that in, it composes with `library_root`."""
        orphan = world.title("Orphan")
        root = world.title("A Root", root=True)

        queue = _ids(client, "titles", "has_intrinsic_parent=false&library_root=false")

        assert orphan in queue
        assert root not in queue
        # And the root is still unparented on its own terms -- the two are separate
        # questions, which is why both filters are needed to ask this one.
        assert root in _ids(client, "titles", "has_intrinsic_parent=false")

    def test_true_agrees_with_membership_intrinsic(self, client, world):
        """`has_intrinsic_parent=true` is the negation of the half `membership` could
        already express, so the two must return the same set or one of them is wrong."""
        child = world.title("Child", "episode")
        world.contains_title(world.title("Parent", "season"), child)
        listed = world.title("Listed")
        world.contains_title(world.title("Collection"), listed, membership=MembershipKind.curated)
        world.title("Orphan")

        assert _ids(client, "titles", "has_intrinsic_parent=true") == _ids(
            client, "titles", "membership=intrinsic"
        )

    def test_a_title_in_several_curated_lists_is_returned_once(self, client, world):
        listed = world.title("Listed")
        for name in ("List A", "List B", "List C"):
            world.contains_title(world.title(name), listed, membership=MembershipKind.curated)

        response = client.get("/api/titles/?has_intrinsic_parent=false&limit=100")
        assert response.status_code == HTTPStatus.OK
        returned = [item["id"] for item in response.json()["items"]]
        assert returned.count(listed) == 1


@pytest.mark.api
@pytest.mark.integration
class TestTheTwoDirectionsPartition:
    """Every row is on exactly one side. A row missing from both is a predicate that
    stopped being a negation; a row on both is a filter that is not exclusive."""

    def test_assets_partition(self, client, world):
        loose = world.asset()
        placed = world.asset()
        world.contains_asset(world.title("Home"), placed)
        listed = world.asset()
        world.contains_asset(world.title("List"), listed, membership=MembershipKind.curated)

        yes = _ids(client, "assets", "has_intrinsic_parent=true")
        no = _ids(client, "assets", "has_intrinsic_parent=false")

        assert yes | no == _all(client, "assets")
        assert yes & no == set()
        assert placed in yes
        assert {loose, listed} <= no

    def test_titles_partition(self, client, world):
        orphan = world.title("Orphan")
        parent = world.title("Parent", "season")
        child = world.title("Child", "episode")
        world.contains_title(parent, child)
        listed = world.title("Listed")
        world.contains_title(world.title("Collection"), listed, membership=MembershipKind.curated)

        yes = _ids(client, "titles", "has_intrinsic_parent=true")
        no = _ids(client, "titles", "has_intrinsic_parent=false")

        assert yes | no == _all(client, "titles")
        assert yes & no == set()
        assert child in yes
        assert {orphan, parent, listed} <= no


@pytest.mark.api
@pytest.mark.integration
class TestItComposesWithTheOtherFilters:
    """A filter that is correct alone and drops rows in combination is the failure that
    reaches production, which is why #94 probed the grid's real query rather than each
    filter separately."""

    def test_assets_with_an_extension_and_no_home(self, client, world):
        loose = world.asset()
        placed = world.asset()
        world.contains_asset(world.title("Home"), placed)

        queue = _ids(client, "assets", "has_intrinsic_parent=false&filename_ext=mkv")

        assert loose in queue
        assert placed not in queue

    def test_titles_unparented_of_one_type(self, client, world):
        movie = world.title("Movie", "movie")
        episode = world.title("Episode", "episode")

        queue = _ids(client, "titles", "has_intrinsic_parent=false&title_type=movie")

        assert movie in queue
        assert episode not in queue

    def test_unparented_but_listed_somewhere(self, client, world):
        """`has_intrinsic_parent=false&membership=curated` -- living nowhere, but
        appearing in a list. A real state, and neither half asks it alone."""
        listed = world.title("Listed")
        world.contains_title(world.title("Collection"), listed, membership=MembershipKind.curated)
        orphan = world.title("Orphan")

        both = _ids(client, "titles", "has_intrinsic_parent=false&membership=curated")

        assert listed in both
        assert orphan not in both
