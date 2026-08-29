"""Integration tests for the filters on GET /api/titles/ (issue #94).

The library grid could previously ask only for a name. These are the filters that make
#91's `library_root` and #90's `membership` reachable at all -- both fields had no
effect until this landed.

Two things are worth more than the individual filters. First, that they compose: the
grid issues several at once, and a filter that works alone but drops rows in combination
would only show up in production. Second, that a filter matching *any of* several values
returns each title once -- a title carrying two of the requested tags must not appear
twice, or `limit` stops being a cap on titles and the keyset cursor is computed over a
set that no longer matches the rows.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories import (
    SQLAlchemyTagRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleRepository,
)
from app.schemas import TagCreateInternal, TitleContentCreateInternal, TitleCreateInternal
from app.schemas.enums import ContentKind, MembershipKind


@pytest.fixture
def make_title(db_session, title_type_ids: dict[str, int]):
    repo = SQLAlchemyTitleRepository(db_session)
    counter = {"n": 0}

    def _make(name: str | None = None, type_code: str = "movie", library_root: bool = False) -> int:
        counter["n"] += 1
        return repo.create(
            TitleCreateInternal(
                name=name or f"Title {counter['n']}",
                title_type_id=title_type_ids[type_code],
                library_root=library_root,
            )
        ).id

    return _make


@pytest.fixture
def make_tag(db_session):
    repo = SQLAlchemyTagRepository(db_session)
    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        return repo.create(TagCreateInternal(name=f"tag-{counter['n']}")).id

    return _make


@pytest.fixture
def tag_title(db_session):
    from app.models import TitleTagORM

    def _tag(title_id: int, tag_id: int) -> None:
        db_session.add(TitleTagORM(title_id=title_id, tag_id=tag_id))
        db_session.commit()

    return _tag


@pytest.fixture
def contain(db_session):
    repo = SQLAlchemyTitleContentRepository(db_session)
    counter = {"n": 0}

    def _contain(
        parent_id: int, child_id: int, membership: MembershipKind = MembershipKind.intrinsic
    ) -> int:
        counter["n"] += 1
        return repo.create(
            TitleContentCreateInternal(
                parent_title_id=parent_id,
                kind=ContentKind.title,
                child_title_id=child_id,
                asset_id=None,
                label=None,
                membership=membership,
                order_key=f"U{counter['n']:05d}",
            )
        ).id

    return _contain


def _ids(client: TestClient, query: str) -> set[int]:
    response = client.get(f"/api/titles/?{query}&limit=500")
    assert response.status_code == HTTPStatus.OK, response.text
    return {item["id"] for item in response.json()["items"]}


@pytest.mark.integration
class TestLibraryRootFilter:

    def test_selects_roots(self, client: TestClient, make_title):
        root = make_title(library_root=True)
        leaf = make_title(library_root=False)

        assert _ids(client, "library_root=true") == {root}

    def test_selects_non_roots(self, client: TestClient, make_title):
        make_title(library_root=True)
        leaf = make_title(library_root=False)

        assert _ids(client, "library_root=false") == {leaf}

    def test_omitting_it_returns_both(self, client: TestClient, make_title):
        root, leaf = make_title(library_root=True), make_title(library_root=False)

        assert _ids(client, "name=Title") == {root, leaf}


@pytest.mark.integration
class TestTitleTypeFilter:

    def test_matches_one_code(self, client: TestClient, make_title):
        movie = make_title(type_code="movie")
        make_title(type_code="season")

        assert _ids(client, "title_type=movie") == {movie}

    def test_matches_any_of_several(self, client: TestClient, make_title):
        movie, tv = make_title(type_code="movie"), make_title(type_code="tv")
        make_title(type_code="season")

        assert _ids(client, "title_type=movie,tv") == {movie, tv}

    def test_codes_are_case_insensitive(self, client: TestClient, make_title):
        """Folded on the input, not the column, so `ix_title_types_code` still serves it."""
        movie = make_title(type_code="movie")

        assert _ids(client, "title_type=MOVIE") == {movie}

    def test_an_unknown_code_matches_nothing(self, client: TestClient, make_title):
        """A grid with a stale type list gets an empty page, not a 422."""
        make_title(type_code="movie")

        assert _ids(client, "title_type=nosuchtype") == set()


@pytest.mark.integration
class TestTagFilter:

    def test_matches_any_of_several_tags(self, client: TestClient, make_title, make_tag, tag_title):
        a, b, c = make_tag(), make_tag(), make_tag()
        first, second, untagged = make_title(), make_title(), make_title()
        tag_title(first, a)
        tag_title(second, b)
        tag_title(untagged, c)

        assert _ids(client, f"tag_ids={a},{b}") == {first, second}

    def test_a_title_with_two_matching_tags_appears_once(
        self, client: TestClient, make_title, make_tag, tag_title
    ):
        """The reason this is an EXISTS and not a join: otherwise `limit` stops being a
        cap on titles and the keyset cursor is computed over duplicated rows."""
        a, b = make_tag(), make_tag()
        title = make_title()
        tag_title(title, a)
        tag_title(title, b)

        items = client.get(f"/api/titles/?tag_ids={a},{b}&limit=500").json()["items"]

        assert [item["id"] for item in items] == [title]

    def test_a_non_numeric_tag_id_is_a_422(self, client: TestClient):
        """A caller-caused condition must not arrive as a 500 from int() in the repository."""
        response = client.get("/api/titles/?tag_ids=abc")

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.integration
class TestParentAndMembershipFilters:

    def test_filters_by_parent(self, client: TestClient, make_title, contain):
        parent, other = make_title(), make_title()
        mine, theirs = make_title(), make_title()
        contain(parent, mine)
        contain(other, theirs)

        assert _ids(client, f"parent_id={parent}") == {mine}

    def test_parent_matches_either_membership(self, client: TestClient, make_title, contain):
        parent = make_title()
        home, listed = make_title(), make_title()
        contain(parent, home)
        contain(parent, listed, membership=MembershipKind.curated)

        assert _ids(client, f"parent_id={parent}") == {home, listed}

    def test_membership_narrows_a_parent(self, client: TestClient, make_title, contain):
        parent = make_title()
        home, listed = make_title(), make_title()
        contain(parent, home)
        contain(parent, listed, membership=MembershipKind.curated)

        assert _ids(client, f"parent_id={parent}&membership=curated") == {listed}
        assert _ids(client, f"parent_id={parent}&membership=intrinsic") == {home}

    def test_membership_alone_asks_whether_any_such_edge_exists(
        self, client: TestClient, make_title, contain
    ):
        homed, listed, orphan = make_title(), make_title(), make_title()
        contain(make_title(), homed)
        contain(make_title(), listed, membership=MembershipKind.curated)

        found = _ids(client, "membership=curated")

        assert listed in found
        assert homed not in found and orphan not in found

    def test_a_title_in_several_curated_lists_appears_once(
        self, client: TestClient, make_title, contain
    ):
        child = make_title()
        contain(make_title(), child)
        for _ in range(3):
            contain(make_title(), child, membership=MembershipKind.curated)

        items = client.get("/api/titles/?membership=curated&limit=500").json()["items"]

        assert [item["id"] for item in items] == [child]


@pytest.mark.integration
class TestTheGridQuery:
    """The combination the library grid will actually issue.

    Documented and probed as one query rather than only as separate filters, because a
    filter that works alone and drops rows in combination is the failure that reaches
    production.
    """

    def test_the_grid_combination(
        self, client: TestClient, make_title, make_tag, tag_title, contain
    ):
        genre = make_tag()
        # What the grid wants: a root, of a browsable type, carrying the genre tag.
        wanted = make_title(name="Wanted Film", type_code="movie", library_root=True)
        tag_title(wanted, genre)

        # One near miss per filter, so each clause is load-bearing in combination.
        not_root = make_title(name="Not Root", type_code="movie", library_root=False)
        tag_title(not_root, genre)
        wrong_type = make_title(name="Wrong Type", type_code="season", library_root=True)
        tag_title(wrong_type, genre)
        untagged = make_title(name="Untagged", type_code="movie", library_root=True)

        found = _ids(client, f"library_root=true&title_type=movie,tv&tag_ids={genre}")

        assert found == {wanted}

    def test_the_grid_combination_paginates(
        self, client: TestClient, make_title, make_tag, tag_title
    ):
        genre = make_tag()
        wanted = []
        for i in range(5):
            title = make_title(name=f"Film {i}", type_code="movie", library_root=True)
            tag_title(title, genre)
            wanted.append(title)

        first = client.get(
            f"/api/titles/?library_root=true&title_type=movie&tag_ids={genre}&limit=2"
        ).json()
        assert len(first["items"]) == 2
        assert first["page"]["next"] is not None

        second = client.get(
            f"/api/titles/?library_root=true&title_type=movie&tag_ids={genre}"
            f"&limit=2&after={first['page']['next']}"
        ).json()

        assert len(second["items"]) == 2
        seen = [i["id"] for i in first["items"]] + [i["id"] for i in second["items"]]
        assert len(set(seen)) == 4
