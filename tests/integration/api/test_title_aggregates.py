"""Integration tests for a title's aggregate counts and totals (issue #96).

Two rules that deliberately differ, because they answer different questions:

- ``child_count`` / ``asset_count`` -- **direct** edges, **every** membership. A
  curated list's size is the number its tile exists to show, so filtering these to
  intrinsic edges would report every curated collection as empty.
- ``total_runtime`` / ``total_size`` -- **recursive** over **intrinsic** edges only,
  deduplicated by asset. A borrowed title must not add its runtime to every list that
  borrowed it, and one file under two cuts must be counted once.

The thing asserted hardest here is the one no correctness test can see: that the
aggregates cost the same number of queries whatever the page size. #49 measured a
per-row query at 14.6s against 263ms at the 500-row cap.
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


@contextmanager
def _statements_touching(engine: Engine, table: str) -> Iterator[list[str]]:
    """Collect every SQL statement issued against ``table`` inside the block.

    Args:
        engine: The engine the request's session is bound to.
        table: Table name to match, case-insensitively, against each statement.

    Yields:
        list[str]: The matching statements, appended as they are executed.
    """
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
def world(db_session: Session, title_type_ids: dict[str, int]):
    """Helpers for building titles, assets and containment edges."""
    titles = SQLAlchemyTitleRepository(db_session)
    assets = SQLAlchemyMediaRepository(db_session)
    contents = SQLAlchemyTitleContentRepository(db_session)
    counter = {"n": 0}
    keys: dict[int, int] = {}

    class World:
        def title(self, name: str = "T", code: str = "movie") -> int:
            return titles.create(
                TitleCreateInternal(name=name, title_type_id=title_type_ids[code])
            ).id

        def asset(self, duration: float = 1.0, size: int = 1) -> int:
            counter["n"] += 1
            n = counter["n"]
            return assets.create(
                AssetCreateInternal(
                    path=f"movies/{n}.mkv",
                    filename=f"{n}.mkv",
                    duration=duration,
                    bitrate=1,
                    container_format="matroska",
                    size=size,
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

    return World()


def _find(items: list[dict], title_id: int) -> dict:
    return next(item for item in items if item["id"] == title_id)


@pytest.mark.api
@pytest.mark.integration
class TestAggregatesAreOptIn:
    """`include=` governs whether the fields are populated at all."""

    def test_absent_without_include(self, client: TestClient, world) -> None:
        """Null, not 0 -- a caller must be able to tell "not asked" from "empty"."""
        title = world.title()
        world.contains_asset(title, world.asset())

        body = _find(client.get("/api/titles/").json()["items"], title)

        assert body["child_count"] is None
        assert body["asset_count"] is None
        assert body["total_runtime"] is None
        assert body["total_size"] is None

    def test_counts_alone_do_not_populate_totals(self, client: TestClient, world) -> None:
        """The tokens are separate so the grid does not pay for the recursive walk."""
        title = world.title()
        world.contains_asset(title, world.asset())

        body = _find(client.get("/api/titles/?include=counts").json()["items"], title)

        assert body["asset_count"] == 1
        assert body["total_runtime"] is None

    def test_an_empty_title_reports_zero_not_null(self, client: TestClient, world) -> None:
        """Asked-for-and-empty is 0; the two must not collapse into each other."""
        title = world.title()

        body = _find(client.get("/api/titles/?include=counts,totals").json()["items"], title)

        assert body["child_count"] == 0
        assert body["asset_count"] == 0
        assert body["total_runtime"] == 0.0
        assert body["total_size"] == 0

    def test_the_detail_view_resolves_both_unconditionally(self, client: TestClient, world) -> None:
        """One row costs one query either way, so `include` buys nothing here."""
        title = world.title()
        world.contains_asset(title, world.asset(duration=12.0, size=34))

        body = client.get(f"/api/titles/{title}").json()

        assert body["asset_count"] == 1
        assert body["total_runtime"] == 12.0
        assert body["total_size"] == 34


@pytest.mark.api
@pytest.mark.integration
class TestCountingRules:
    """The two rules, and the cases that tell them apart."""

    def test_a_curated_list_reports_its_size(self, client: TestClient, world) -> None:
        """The rule #96's text got wrong.

        Filtering counts to `membership = 'intrinsic'` would report this list as
        containing nothing, which is precisely the number its tile exists to show.
        """
        collection = world.title("Best of 2020")
        for _ in range(3):
            world.contains_title(collection, world.title(), membership=MembershipKind.curated)

        body = _find(client.get("/api/titles/?include=counts").json()["items"], collection)

        assert body["child_count"] == 3

    def test_a_child_under_two_parents_counts_for_both(self, client: TestClient, world) -> None:
        """The fixture #96 asks for: one intrinsic parent, one curated.

        A fixture with one parent per child cannot distinguish a correct aggregate
        from a broken one -- both pass.
        """
        home = world.title("Season 1")
        collection = world.title("Staff Picks")
        child = world.title("Episode 1")
        world.contains_title(home, child, membership=MembershipKind.intrinsic)
        world.contains_title(collection, child, membership=MembershipKind.curated)

        items = client.get("/api/titles/?include=counts").json()["items"]

        assert _find(items, home)["child_count"] == 1
        assert _find(items, collection)["child_count"] == 1

    def test_counts_are_direct_while_totals_are_deep(self, client: TestClient, world) -> None:
        """The same hierarchy, counted two different ways on purpose."""
        season = world.title("Season 1")
        episode = world.title("Episode 1")
        world.contains_title(season, episode)
        world.contains_asset(episode, world.asset(duration=90.0, size=700))

        body = _find(client.get("/api/titles/?include=counts,totals").json()["items"], season)

        assert body["child_count"] == 1
        assert body["asset_count"] == 0, "the grandchild asset is not a direct edge"
        assert body["total_runtime"] == 90.0, "but it is beneath the season"

    def test_totals_ignore_curated_edges(self, client: TestClient, world) -> None:
        """A borrowed title's runtime stays with its home, not with the list."""
        home = world.title("Season 1")
        collection = world.title("Staff Picks")
        child = world.title("Episode 1")
        world.contains_title(home, child, membership=MembershipKind.intrinsic)
        world.contains_title(collection, child, membership=MembershipKind.curated)
        world.contains_asset(child, world.asset(duration=42.0, size=99))

        items = client.get("/api/titles/?include=counts,totals").json()["items"]

        assert _find(items, home)["total_runtime"] == 42.0
        assert _find(items, collection)["total_runtime"] == 0.0
        # The same edge that contributed no runtime still counts as a child
        assert _find(items, collection)["child_count"] == 1

    def test_an_asset_under_two_titles_is_counted_once(self, client: TestClient, world) -> None:
        """The deduplication that actually matters, and it is not the one #96 names.

        `uq_parent_asset_once` is scoped to a single parent, so the same file under two
        cuts is ordinary. Summing the join directly would double its runtime.
        """
        season = world.title("Season 1")
        cut_a = world.title("Theatrical")
        cut_b = world.title("Extended")
        world.contains_title(season, cut_a)
        world.contains_title(season, cut_b)
        shared = world.asset(duration=50.0, size=500)
        world.contains_asset(cut_a, shared)
        world.contains_asset(cut_b, shared)

        body = _find(client.get("/api/titles/?include=totals").json()["items"], season)

        assert body["total_runtime"] == 50.0, "a shared asset must not be summed twice"
        assert body["total_size"] == 500


@pytest.mark.api
@pytest.mark.integration
class TestAggregateQueryCount:
    """Guard the cost against the N+1 regression #49 recorded.

    An aggregate evaluated per tile is #49 wearing different clothes, and it is
    invisible to every correctness test above -- the response body is identical
    either way -- so it is asserted as a query count.
    """

    def _build(self, world, n: int) -> None:
        for _ in range(n):
            season = world.title("Season", "season")
            episode = world.title("Episode", "episode")
            world.contains_title(season, episode)
            world.contains_asset(episode, world.asset(duration=10.0, size=10))

    def test_counts_do_not_scale_with_rows_returned(
        self, client: TestClient, world, _test_engine: Engine
    ) -> None:
        self._build(world, 6)

        counts: dict[int, int] = {}
        for limit in (2, 6):
            with _statements_touching(_test_engine, "title_contents") as statements:
                response = client.get(f"/api/titles/?include=counts&limit={limit}")
            assert response.status_code == HTTPStatus.OK
            items = response.json()["items"]
            assert len(items) == limit
            # Something must actually be populated, or the count proves nothing
            assert any(item["child_count"] or item["asset_count"] for item in items)
            counts[limit] = len(statements)

        assert counts[2] == counts[6], (
            f"title_contents queries scaled with page size: "
            f"{counts[2]} for 2 rows, {counts[6]} for 6 rows"
        )
        assert counts[6] <= 2, f"expected one aggregate query per page, got {counts[6]}"

    def test_totals_do_not_scale_with_rows_returned(
        self, client: TestClient, world, _test_engine: Engine
    ) -> None:
        self._build(world, 6)

        counts: dict[int, int] = {}
        for limit in (2, 6):
            with _statements_touching(_test_engine, "title_contents") as statements:
                response = client.get(f"/api/titles/?include=totals&limit={limit}")
            assert response.status_code == HTTPStatus.OK
            items = response.json()["items"]
            assert len(items) == limit
            assert any(item["total_runtime"] for item in items)
            counts[limit] = len(statements)

        assert counts[2] == counts[6], (
            f"title_contents queries scaled with page size: "
            f"{counts[2]} for 2 rows, {counts[6]} for 6 rows"
        )
        assert counts[6] <= 2, f"expected one recursive walk per page, got {counts[6]}"

    def test_depth_does_not_add_queries(
        self, client: TestClient, world, _test_engine: Engine
    ) -> None:
        """Depth is handled inside the recursive CTE, so it must not cost more."""
        shallow = world.title("Shallow")
        world.contains_asset(shallow, world.asset())

        deep = world.title("Deep")
        node = deep
        for _ in range(4):
            child = world.title("Level")
            world.contains_title(node, child)
            node = child
        world.contains_asset(node, world.asset())

        with _statements_touching(_test_engine, "title_contents") as statements:
            response = client.get("/api/titles/?include=totals")
        assert response.status_code == HTTPStatus.OK

        assert (
            len(statements) <= 2
        ), f"a deeper hierarchy must not add queries, got {len(statements)}"
