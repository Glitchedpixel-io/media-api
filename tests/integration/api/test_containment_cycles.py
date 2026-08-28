"""Integration tests for containment cycle prevention (issue #88).

Containment is a DAG and nothing in the schema could say so: Postgres cannot express
reachability as a constraint, so the self-edge case is declarative and the rest is a
service guard. What matters here is that both halves hold, that they cover the patch
path as well as the insert path, and that the reachability walk terminates on a
database that already contains a cycle -- which every deployment does until its
migration runs.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from app.repositories import SQLAlchemyTitleContentRepository, SQLAlchemyTitleRepository
from app.repositories.errors import CheckViolation
from app.schemas import TitleContentCreateInternal, TitleCreateInternal
from app.schemas.enums import ContentKind


@pytest.fixture
def make_title(db_session, title_type_ids: dict[str, int]):
    repo = SQLAlchemyTitleRepository(db_session)
    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        return repo.create(
            TitleCreateInternal(name=f"Title {counter['n']}", title_type_id=title_type_ids["movie"])
        ).id

    return _make


@pytest.fixture
def contain(db_session):
    """Create a parent -> child containment edge directly, bypassing the service."""
    repo = SQLAlchemyTitleContentRepository(db_session)
    counter = {"n": 0}

    def _contain(parent_id: int, child_id: int) -> int:
        counter["n"] += 1
        return repo.create(
            TitleContentCreateInternal(
                parent_title_id=parent_id,
                kind=ContentKind.title,
                child_title_id=child_id,
                asset_id=None,
                label=None,
                order_key="U" + "U" * counter["n"],
            )
        ).id

    return _contain


def _add_content(client: TestClient, parent_id: int, child_id: int):
    return client.post(
        f"/api/titles/{parent_id}/contents",
        json={"kind": "title", "child_title_id": child_id},
    )


@pytest.mark.integration
class TestSelfContainment:

    def test_a_title_cannot_contain_itself(self, client: TestClient, make_title):
        title_id = make_title()

        response = _add_content(client, title_id, title_id)

        assert response.status_code == HTTPStatus.CONFLICT

    def test_the_database_refuses_it_too(self, db_session, make_title, contain):
        """The service guard is not the only line: a writer reaching the table directly
        is exactly how 263 of these arrived in production."""
        title_id = make_title()

        # CheckViolation rather than the constraint name: `_safe_commit` translates the
        # driver error and deliberately does not carry the name through, so asserting
        # on the message would be testing the translation layer instead of the schema.
        with pytest.raises(CheckViolation):
            contain(title_id, title_id)


@pytest.mark.integration
class TestCycles:

    def test_a_two_step_cycle_is_refused(self, client: TestClient, make_title):
        a, b = make_title(), make_title()
        assert _add_content(client, a, b).status_code == HTTPStatus.CREATED

        response = _add_content(client, b, a)

        assert response.status_code == HTTPStatus.CONFLICT

    def test_a_longer_cycle_is_refused(self, client: TestClient, make_title):
        a, b, c = make_title(), make_title(), make_title()
        _add_content(client, a, b)
        _add_content(client, b, c)

        response = _add_content(client, c, a)

        assert response.status_code == HTTPStatus.CONFLICT

    def test_a_diamond_is_still_allowed(self, client: TestClient, make_title):
        """Containment is a DAG, not a tree: two parents may share a child, and that is
        not a cycle. A guard that rejected this would break legitimate structure."""
        top, left, right, bottom = (make_title() for _ in range(4))
        _add_content(client, top, left)
        _add_content(client, top, right)
        assert _add_content(client, left, bottom).status_code == HTTPStatus.CREATED
        assert _add_content(client, right, bottom).status_code == HTTPStatus.CREATED

    def test_the_same_child_under_two_parents_is_allowed(self, client: TestClient, make_title):
        a, b, shared = make_title(), make_title(), make_title()
        assert _add_content(client, a, shared).status_code == HTTPStatus.CREATED
        assert _add_content(client, b, shared).status_code == HTTPStatus.CREATED

    def test_a_patch_cannot_repoint_a_row_into_a_cycle(self, client: TestClient, make_title):
        """The shorter path to the same invalid state."""
        a, b, c = make_title(), make_title(), make_title()
        _add_content(client, a, b)
        created = _add_content(client, b, c).json()

        response = client.patch(
            f"/api/titles/{b}/contents/{created['id']}",
            json={"child_title_id": a},
        )

        assert response.status_code == HTTPStatus.CONFLICT


@pytest.mark.integration
class TestWalkTerminates:

    def test_the_guard_survives_a_cycle_that_already_exists(
        self, db_session, client: TestClient, make_title, contain
    ):
        """Until the migration runs, a live database still holds cycles. The walk has to
        terminate on one rather than hang, or the guard cannot be deployed to the very
        databases that need it.

        The constraint is dropped for the duration so a cycle can be planted at all --
        which is precisely the state this deployment is upgrading from.
        """
        a, b, c = make_title(), make_title(), make_title()
        db_session.execute(
            sql_text("ALTER TABLE title_contents DROP CONSTRAINT no_self_containment_chk")
        )
        contain(a, b)
        contain(b, a)
        db_session.commit()

        try:
            response = _add_content(client, c, a)
        finally:
            db_session.execute(
                sql_text(
                    "ALTER TABLE title_contents ADD CONSTRAINT no_self_containment_chk "
                    "CHECK (child_title_id IS DISTINCT FROM parent_title_id)"
                )
            )
            db_session.commit()

        # c -> a is not itself a cycle; the point is that the walk returned at all.
        assert response.status_code == HTTPStatus.CREATED
