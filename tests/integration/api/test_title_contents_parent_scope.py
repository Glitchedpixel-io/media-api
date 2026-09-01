# tests/integration/api/test_title_contents_parent_scope.py

"""`{parent_title_id}` names the edge's current parent, on every contents write (#185).

Before #185 the path segment meant three different things. It was the *destination* on
`PATCH .../contents/{id}` and on `PATCH .../contents/{id}/reorder` -- both relocated the
edge to whatever title the URL named -- and it was decorative on `DELETE`, which removed
the edge by id alone. None of the three checked that the edge was under that parent to
begin with.

The consequence worth a regression test is the first one below: `reorder` moved an edge
across parents without calling `_reject_cycle`, so an edge the API refuses to *create*
could be arrived at by moving one that already existed. A containment cycle is not a
cosmetic problem -- every consumer that walks containment for a breadcrumb or a tree hangs
on one unless it carries its own defence, which is why the guard exists at all (#88).

Each test here was written against the pre-fix code first and observed to fail.
"""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import (
    MediaRepository,
    TitleRepository,
)
from app.schemas import AssetCreateInternal
from tests.factories import (
    AssetReadFactory,
    TitleReadFactory,
    get_title_internal,
)


def _title(title_repository: TitleRepository) -> int:
    """A bare title, returned as its id."""
    return title_repository.create(get_title_internal(TitleReadFactory())).id


def _asset(media_repository: MediaRepository) -> int:
    """A bare asset, returned as its id."""
    created = media_repository.create(
        AssetCreateInternal(
            **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
        )
    )
    return created.id


def _attach(client: TestClient, parent: int, child: int, membership: str = "intrinsic") -> int:
    """Put `child` under `parent` and return the containment row's id."""
    response = client.post(
        f"/api/titles/{parent}/contents",
        json={"kind": "title", "child_title_id": child, "membership": membership},
    )
    assert response.status_code == HTTPStatus.CREATED, response.text
    return response.json()["id"]


def _contents(client: TestClient, parent: int) -> list[dict]:
    response = client.get(f"/api/titles/{parent}/contents")
    assert response.status_code == HTTPStatus.OK, response.text
    return response.json()


@pytest.mark.api
@pytest.mark.integration
class TestReorderCannotOpenACycle:
    """The hole that made #185 a bug rather than an inconsistency."""

    def test_reorder_cannot_reach_a_cycle_that_create_refuses(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        """The same edge, refused by POST, must not be granted by reorder.

        A contains B. An edge pointing at A is parked under C. Attaching A under B
        directly is a 409; moving that parked edge under B reached the identical state
        with a 200, leaving A ⊃ B and B ⊃ A.
        """
        a = _title(title_repository)
        b = _title(title_repository)
        c = _title(title_repository)
        _attach(client, a, b)
        parked = _attach(client, c, a, membership="curated")

        # The direct route to this edge is refused, and says why.
        direct = client.post(
            f"/api/titles/{b}/contents",
            json={"kind": "title", "child_title_id": a, "membership": "curated"},
        )
        assert direct.status_code == HTTPStatus.CONFLICT
        assert "cycle" in direct.json()["detail"]

        # The indirect route must be refused too. Addressed at B, the edge is not under
        # B, so this is now a 404 -- the move that would have made it a 409 has its own
        # endpoint (#178), and that is where the cycle check answers.
        moved = client.patch(
            f"/api/titles/{b}/contents/{parked}/reorder", params={"position": "end"}
        )
        assert moved.status_code == HTTPStatus.NOT_FOUND, moved.text

        # And the structure is unchanged: B holds nothing, C still holds the edge.
        assert _contents(client, b) == []
        assert [row["id"] for row in _contents(client, c)] == [parked]

    def test_reorder_within_the_parent_still_works(
        self,
        client: TestClient,
        title_repository: TitleRepository,
        media_repository: MediaRepository,
    ) -> None:
        """Scoping the path parent must not break the reorder the route is for."""
        parent = _title(title_repository)
        edges = []
        for _ in range(3):
            asset_id = _asset(media_repository)
            response = client.post(
                f"/api/titles/{parent}/contents",
                json={"kind": "asset", "asset_id": asset_id},
            )
            assert response.status_code == HTTPStatus.CREATED, response.text
            edges.append(response.json()["id"])

        response = client.patch(
            f"/api/titles/{parent}/contents/{edges[2]}/reorder", params={"position": "start"}
        )
        assert response.status_code == HTTPStatus.OK, response.text
        assert [row["id"] for row in _contents(client, parent)] == [edges[2], edges[0], edges[1]]


@pytest.mark.api
@pytest.mark.integration
class TestTheEdgeMustBeUnderTheParent:
    """One missing check, wearing four different faces."""

    def test_reorder_through_an_unrelated_parent_is_404(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        a = _title(title_repository)
        unrelated = _title(title_repository)
        edge = _attach(client, a, _title(title_repository))

        response = client.patch(
            f"/api/titles/{unrelated}/contents/{edge}/reorder", params={"position": "end"}
        )

        assert response.status_code == HTTPStatus.NOT_FOUND, response.text
        assert [row["id"] for row in _contents(client, a)] == [edge]
        assert _contents(client, unrelated) == []

    def test_patch_through_an_unrelated_parent_is_404(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        """A label edit addressed at the wrong title relocated the edge to it."""
        a = _title(title_repository)
        unrelated = _title(title_repository)
        edge = _attach(client, a, _title(title_repository))

        response = client.patch(
            f"/api/titles/{unrelated}/contents/{edge}", json={"label": "moved?"}
        )

        assert response.status_code == HTTPStatus.NOT_FOUND, response.text
        assert [row["id"] for row in _contents(client, a)] == [edge]
        assert _contents(client, unrelated) == []

    def test_delete_through_an_unrelated_parent_is_404(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        a = _title(title_repository)
        unrelated = _title(title_repository)
        edge = _attach(client, a, _title(title_repository))

        response = client.delete(f"/api/titles/{unrelated}/contents/{edge}")

        assert response.status_code == HTTPStatus.NOT_FOUND, response.text
        assert [row["id"] for row in _contents(client, a)] == [edge]

    def test_a_patch_no_longer_relocates_the_edge(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        """Addressed at its own parent, a patch edits and nothing else."""
        parent = _title(title_repository)
        edge = _attach(client, parent, _title(title_repository))

        response = client.patch(f"/api/titles/{parent}/contents/{edge}", json={"label": "renamed"})

        assert response.status_code == HTTPStatus.OK, response.text
        body = response.json()
        assert body["label"] == "renamed"
        assert body["parent_title_id"] == parent
        assert [row["id"] for row in _contents(client, parent)] == [edge]

    def test_an_unknown_parent_is_still_404(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        """The pre-existing "no such title" case keeps its status."""
        parent = _title(title_repository)
        edge = _attach(client, parent, _title(title_repository))

        for response in (
            client.delete(f"/api/titles/{2**31 - 1}/contents/{edge}"),
            client.patch(f"/api/titles/{2**31 - 1}/contents/{edge}", json={"label": "x"}),
            client.patch(
                f"/api/titles/{2**31 - 1}/contents/{edge}/reorder", params={"position": "end"}
            ),
        ):
            assert response.status_code == HTTPStatus.NOT_FOUND, response.text

    def test_an_unknown_edge_under_a_real_parent_is_404(
        self,
        client: TestClient,
        title_repository: TitleRepository,
    ) -> None:
        parent = _title(title_repository)

        for response in (
            client.delete(f"/api/titles/{parent}/contents/{2**31 - 1}"),
            client.patch(f"/api/titles/{parent}/contents/{2**31 - 1}", json={"label": "x"}),
            client.patch(
                f"/api/titles/{parent}/contents/{2**31 - 1}/reorder", params={"position": "end"}
            ),
        ):
            assert response.status_code == HTTPStatus.NOT_FOUND, response.text
