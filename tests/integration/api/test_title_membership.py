"""Integration tests for intrinsic vs curated containment (issue #90).

What matters here is the distinction the column exists to draw. A title has one home,
so a second *intrinsic* parent is refused; it can appear in any number of curated
lists, so those are not refused, and a test that only proved the refusal would leave
the feature indistinguishable from a plain unique constraint.

The rest guards the paths a new field silently falls out of: `create_positioned`
enumerates the fields it copies, the patch model is built to omit `membership`
entirely, and the database default has to survive an insert that never names the
column -- which is how rows actually arrive here from the producer #125 found.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.repositories import (
    SQLAlchemyMediaRepository,
    SQLAlchemyTitleRepository,
)
from app.schemas import AssetCreateInternal, TitleCreateInternal
from tests.factories import AssetReadFactory


@pytest.fixture
def make_title(db_session, title_type_ids: dict[str, int]):
    repo = SQLAlchemyTitleRepository(db_session)
    counter = {"n": 0}

    def _make(name: str | None = None, type_code: str = "movie") -> int:
        counter["n"] += 1
        return repo.create(
            TitleCreateInternal(
                name=name or f"Title {counter['n']}",
                title_type_id=title_type_ids[type_code],
            )
        ).id

    return _make


@pytest.fixture
def make_asset(db_session):
    repo = SQLAlchemyMediaRepository(db_session)
    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        asset = AssetReadFactory()
        return repo.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        ).id

    return _make


def _link(client: TestClient, parent: int, child: int, membership: str | None = None) -> dict:
    body: dict[str, object] = {"kind": "title", "child_title_id": child}
    if membership is not None:
        body["membership"] = membership
    return client.post(f"/api/titles/{parent}/contents", json=body).json()


@pytest.mark.integration
class TestMembershipOnWrite:

    def test_defaults_to_intrinsic(self, client: TestClient, make_title):
        parent, child = make_title(), make_title()

        response = client.post(
            f"/api/titles/{parent}/contents", json={"kind": "title", "child_title_id": child}
        )

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["membership"] == "intrinsic"

    def test_curated_can_be_set_on_create(self, client: TestClient, make_title):
        parent, child = make_title(), make_title()

        response = client.post(
            f"/api/titles/{parent}/contents",
            json={"kind": "title", "child_title_id": child, "membership": "curated"},
        )

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["membership"] == "curated"

    def test_the_positioned_route_preserves_membership(self, client: TestClient, make_title):
        """`create_positioned` copies fields by name, so a new one can be dropped silently."""
        parent, child = make_title(), make_title()

        response = client.post(
            f"/api/titles/{parent}/contents/positioned?position=start",
            json={"kind": "title", "child_title_id": child, "membership": "curated"},
        )

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["membership"] == "curated"

    def test_an_unknown_membership_is_rejected(self, client: TestClient, make_title):
        parent, child = make_title(), make_title()

        response = client.post(
            f"/api/titles/{parent}/contents",
            json={"kind": "title", "child_title_id": child, "membership": "borrowed"},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.integration
class TestOneIntrinsicParent:

    def test_a_second_intrinsic_parent_is_refused(self, client: TestClient, make_title):
        first, second, child = make_title(), make_title(), make_title()
        _link(client, first, child)

        response = client.post(
            f"/api/titles/{second}/contents", json={"kind": "title", "child_title_id": child}
        )

        assert response.status_code == HTTPStatus.CONFLICT
        # The message names the edge it collided with; the bare index violation would not.
        assert str(child) in response.json()["detail"]

    def test_curated_membership_is_not_limited(self, client: TestClient, make_title):
        """The point of the distinction: a title appears in as many lists as you like."""
        child = make_title()
        _link(client, make_title(), child)  # its home

        for _ in range(3):
            response = client.post(
                f"/api/titles/{make_title()}/contents",
                json={"kind": "title", "child_title_id": child, "membership": "curated"},
            )
            assert response.status_code == HTTPStatus.CREATED

        assert len(client.get(f"/api/titles/{child}/parents").json()) == 4

    def test_a_curated_edge_does_not_consume_the_intrinsic_slot(
        self, client: TestClient, make_title
    ):
        """Listing a title first must not stop it acquiring a home afterwards."""
        child = make_title()
        _link(client, make_title(), child, membership="curated")

        response = client.post(
            f"/api/titles/{make_title()}/contents",
            json={"kind": "title", "child_title_id": child},
        )

        assert response.status_code == HTTPStatus.CREATED

    def test_an_asset_may_belong_to_several_titles(
        self, client: TestClient, make_title, make_asset
    ):
        """The constraint covers title edges only -- one file can back two cuts."""
        asset = make_asset()

        for _ in range(3):
            response = client.post(
                f"/api/titles/{make_title()}/contents",
                json={"kind": "asset", "asset_id": asset},
            )
            assert response.status_code == HTTPStatus.CREATED

    def test_the_database_refuses_it_even_without_the_service(self, db_session, make_title):
        """The guard has to hold for a writer that never reaches the service layer.

        #125's 263 self-containment rows arrived from exactly such a producer, which is
        why the rule is an index and not only a check in `TitleContentService`.
        """
        first, second, child = make_title(), make_title(), make_title()
        db_session.execute(
            text(
                "INSERT INTO title_contents (parent_title_id, kind, child_title_id, position) "
                "VALUES (:p, 'title', :c, 0)"
            ),
            {"p": first, "c": child},
        )
        db_session.commit()

        with pytest.raises(Exception):
            db_session.execute(
                text(
                    "INSERT INTO title_contents "
                    "(parent_title_id, kind, child_title_id, position) "
                    "VALUES (:p, 'title', :c, 0)"
                ),
                {"p": second, "c": child},
            )
            db_session.commit()
        db_session.rollback()

    def test_a_direct_insert_defaults_to_intrinsic(self, db_session, make_title):
        """The external writer names no membership, so the default is what classifies it."""
        parent, child = make_title(), make_title()
        db_session.execute(
            text(
                "INSERT INTO title_contents (parent_title_id, kind, child_title_id, position) "
                "VALUES (:p, 'title', :c, 0)"
            ),
            {"p": parent, "c": child},
        )
        db_session.commit()

        stored = db_session.execute(
            text("SELECT membership FROM title_contents WHERE child_title_id = :c"),
            {"c": child},
        ).scalar()

        assert stored == "intrinsic"


@pytest.mark.integration
class TestMembershipIsNotPatchable:

    def test_patching_membership_is_rejected(self, client: TestClient, make_title):
        """Absence from the patch model is the enforcement -- extra fields are forbidden."""
        parent, child = make_title(), make_title()
        edge = _link(client, parent, child)

        response = client.patch(
            f"/api/titles/{parent}/contents/{edge['id']}", json={"membership": "curated"}
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_patching_a_label_leaves_membership_alone(self, client: TestClient, make_title):
        parent, child = make_title(), make_title()
        edge = _link(client, parent, child, membership="curated")

        response = client.patch(
            f"/api/titles/{parent}/contents/{edge['id']}", json={"label": "Renamed"}
        )

        assert response.status_code == HTTPStatus.OK
        assert response.json()["label"] == "Renamed"
        assert response.json()["membership"] == "curated"

    def test_repointing_an_intrinsic_edge_at_a_taken_child_is_refused(
        self, client: TestClient, make_title
    ):
        """The patch path reaches the same invalid state as the insert path."""
        home, other, spare = make_title(), make_title(), make_title()
        taken = make_title()
        _link(client, home, taken)
        edge = _link(client, other, spare)

        response = client.patch(
            f"/api/titles/{other}/contents/{edge['id']}", json={"child_title_id": taken}
        )

        assert response.status_code == HTTPStatus.CONFLICT

    def test_repointing_an_edge_at_its_own_child_is_not_a_conflict(
        self, client: TestClient, make_title
    ):
        """A row must not collide with itself."""
        parent, child = make_title(), make_title()
        edge = _link(client, parent, child)

        response = client.patch(
            f"/api/titles/{parent}/contents/{edge['id']}",
            json={"child_title_id": child, "label": "Same child"},
        )

        assert response.status_code == HTTPStatus.OK


@pytest.mark.integration
class TestMembershipOnRead:

    def test_contents_expose_membership(self, client: TestClient, make_title):
        parent, home_child, listed_child = make_title(), make_title(), make_title()
        _link(client, parent, home_child)
        _link(client, parent, listed_child, membership="curated")

        body = client.get(f"/api/titles/{parent}/contents").json()

        assert {row["child_title_id"]: row["membership"] for row in body} == {
            home_child: "intrinsic",
            listed_child: "curated",
        }

    def test_parents_expose_membership(self, client: TestClient, make_title):
        """What makes a breadcrumb resolvable: which of these parents is the home."""
        child = make_title()
        _link(client, make_title("A Home"), child)
        _link(client, make_title("A List"), child, membership="curated")

        body = client.get(f"/api/titles/{child}/parents").json()

        assert {row["parent_title"]["name"]: row["membership"] for row in body} == {
            "A Home": "intrinsic",
            "A List": "curated",
        }
