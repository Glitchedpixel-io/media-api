"""Integration tests for a title's parents (issue #89).

The upward counterpart of `GET /api/assets/{asset_id}/titles`. What matters here is
that it returns the containment row rather than a bare title -- the label and order a
title carries within a parent are only meaningful on the edge -- that an unknown title
is distinguishable from a title with no parents, and that it stays immediate-parents-only
rather than quietly walking the ancestry.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories import (
    SQLAlchemyMediaRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleRepository,
)
from app.schemas import AssetCreateInternal, TitleContentCreateInternal, TitleCreateInternal
from app.schemas.enums import ContentKind


@pytest.fixture
def make_title(db_session, title_type_ids: dict[str, int]):
    repo = SQLAlchemyTitleRepository(db_session)
    counter = {"n": 0}

    def _make(name: str | None = None) -> int:
        counter["n"] += 1
        return repo.create(
            TitleCreateInternal(
                name=name or f"Title {counter['n']}", title_type_id=title_type_ids["movie"]
            )
        ).id

    return _make


@pytest.fixture
def contain(db_session):
    """Create a parent -> child containment edge directly."""
    repo = SQLAlchemyTitleContentRepository(db_session)
    counter = {"n": 0}

    def _contain(parent_id: int, child_id: int, label: str | None = None) -> int:
        counter["n"] += 1
        return repo.create(
            TitleContentCreateInternal(
                parent_title_id=parent_id,
                kind=ContentKind.title,
                child_title_id=child_id,
                asset_id=None,
                label=label,
                order_key="U" + "U" * counter["n"],
            )
        ).id

    return _contain


@pytest.mark.integration
class TestParents:

    def test_returns_the_immediate_parent(self, client: TestClient, make_title, contain):
        parent = make_title("A Series")
        child = make_title("A Season")
        contain(parent, child)

        body = client.get(f"/api/titles/{child}/parents").json()

        assert len(body) == 1
        assert body[0]["parent_title"]["id"] == parent
        assert body[0]["parent_title"]["name"] == "A Series"

    def test_returns_the_edge_not_just_the_parent(self, client: TestClient, make_title, contain):
        """The label and order a title carries live on the containment row, so returning
        a bare title would drop what distinguishes one membership from another."""
        parent = make_title()
        child = make_title()
        contain(parent, child, label="Episode 1")

        body = client.get(f"/api/titles/{child}/parents").json()

        assert body[0]["label"] == "Episode 1"
        assert body[0]["order_key"]
        assert body[0]["child_title_id"] == child

    def test_a_root_title_has_no_parents(self, client: TestClient, make_title):
        """Most titles are roots -- 504 of 1,585 have a parent -- so empty is ordinary."""
        response = client.get(f"/api/titles/{make_title()}/parents")

        assert response.status_code == HTTPStatus.OK
        assert response.json() == []

    def test_an_unknown_title_is_404_not_an_empty_list(self, client: TestClient):
        """Otherwise a breadcrumb built on a bad id silently renders a root."""
        assert client.get("/api/titles/99999999/parents").status_code == HTTPStatus.NOT_FOUND

    def test_several_parents_are_all_returned(self, client: TestClient, make_title, contain):
        """No title has two parents in the current data, which says more about there
        being no easy way to create one than about the need: nothing in the UI offers
        it, so nobody has. This route is part of what makes it expressible, so the
        multi-parent case is first-class here rather than a schema curiosity."""
        left = make_title("Alpha")
        right = make_title("Beta")
        child = make_title()
        contain(left, child)
        contain(right, child)

        body = client.get(f"/api/titles/{child}/parents").json()

        assert {row["parent_title"]["id"] for row in body} == {left, right}

    def test_parents_are_ordered_by_name(self, client: TestClient, make_title, contain):
        child = make_title()
        contain(make_title("Zulu"), child)
        contain(make_title("Alpha"), child)

        body = client.get(f"/api/titles/{child}/parents").json()

        assert [row["parent_title"]["name"] for row in body] == ["Alpha", "Zulu"]

    def test_it_does_not_walk_the_ancestry(self, client: TestClient, make_title, contain):
        """Immediate parents only. A grandparent appearing here would make the response
        a set of ancestors, which is a different question with a different answer once
        a title has more than one parent."""
        grandparent = make_title()
        parent = make_title()
        child = make_title()
        contain(grandparent, parent)
        contain(parent, child)

        body = client.get(f"/api/titles/{child}/parents").json()

        assert [row["parent_title"]["id"] for row in body] == [parent]

    def test_a_parent_reached_through_an_asset_is_not_a_title_parent(
        self, client: TestClient, db_session, make_title
    ):
        """Containment carries assets as well as titles. An asset's parents belong to
        the asset route; they are not this title's parents."""
        asset_id = (
            SQLAlchemyMediaRepository(db_session)
            .create(
                AssetCreateInternal(
                    path="movies/x.mkv",
                    filename="x.mkv",
                    duration=1.0,
                    bitrate=1,
                    container_format="matroska",
                    size=1,
                    mtime=None,
                    last_seen=None,
                    master_asset_id=None,
                )
            )
            .id
        )
        holder = make_title()
        subject = make_title()
        SQLAlchemyTitleContentRepository(db_session).create(
            TitleContentCreateInternal(
                parent_title_id=holder,
                kind=ContentKind.asset,
                child_title_id=None,
                asset_id=asset_id,
                label=None,
                order_key="UU",
            )
        )

        assert client.get(f"/api/titles/{subject}/parents").json() == []
        assert len(client.get(f"/api/assets/{asset_id}/titles").json()) == 1
