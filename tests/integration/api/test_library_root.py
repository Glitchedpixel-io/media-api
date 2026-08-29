"""Integration tests for the library_root flag on Title (issue #91).

The flag decides what the library grid is a grid *of*, and it is deliberately stored
rather than derived: parent absence is the closest structural proxy and is still wrong,
because a curated collection has no parent and is not a library root.

So what matters here is that it is a plain settable field with a conservative default,
and that it survives the two write paths independently -- PATCH and PUT differ in how
they treat an omitted field, and a boolean is exactly the type where `exclude_none`
handling tends to go wrong.

The backfill rule itself is a migration concern and is not exercised here: this suite
builds its schema from the models via `create_all`, so no migration runs. It was
verified against a scratch database seeded to the production shape -- see the migration.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient


def _payload(**overrides) -> dict:
    body = {"name": "A Film", "title_type": "movie"}
    body.update(overrides)
    return body


@pytest.mark.integration
class TestLibraryRootOnWrite:

    def test_defaults_to_false(self, client: TestClient):
        """Not offered until something says so; the backfill is what states it for
        everything that already existed."""
        response = client.post("/api/titles/", json=_payload())

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["library_root"] is False

    def test_can_be_set_on_create(self, client: TestClient):
        response = client.post("/api/titles/", json=_payload(library_root=True))

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["library_root"] is True

    def test_can_be_set_by_patch(self, client: TestClient):
        title_id = client.post("/api/titles/", json=_payload()).json()["id"]

        response = client.patch(f"/api/titles/{title_id}", json={"library_root": True})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["library_root"] is True

    def test_can_be_cleared_by_patch(self, client: TestClient):
        """False is not None. A patch that clears the flag must not be read as omitting
        it, which is the failure mode `exclude_none` invites for a boolean."""
        title_id = client.post("/api/titles/", json=_payload(library_root=True)).json()["id"]

        response = client.patch(f"/api/titles/{title_id}", json={"library_root": False})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["library_root"] is False

    def test_a_patch_that_omits_it_leaves_it_alone(self, client: TestClient):
        title_id = client.post("/api/titles/", json=_payload(library_root=True)).json()["id"]

        response = client.patch(f"/api/titles/{title_id}", json={"name": "Renamed"})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["name"] == "Renamed"
        assert response.json()["library_root"] is True

    def test_can_be_set_by_put(self, client: TestClient):
        title_id = client.post("/api/titles/", json=_payload()).json()["id"]

        response = client.put(f"/api/titles/{title_id}", json=_payload(library_root=True))

        assert response.status_code == HTTPStatus.OK
        assert response.json()["library_root"] is True


@pytest.mark.integration
class TestLibraryRootOnRead:

    def test_exposed_on_the_detail_route(self, client: TestClient):
        title_id = client.post("/api/titles/", json=_payload(library_root=True)).json()["id"]

        body = client.get(f"/api/titles/{title_id}").json()

        assert body["library_root"] is True

    def test_exposed_on_the_list_route(self, client: TestClient):
        """The grid reads this per tile, so it has to survive the list serialiser too."""
        root = client.post("/api/titles/", json=_payload(library_root=True)).json()["id"]
        leaf = client.post("/api/titles/", json=_payload(name="An Episode")).json()["id"]

        items = client.get("/api/titles/?limit=500").json()["items"]

        by_id = {item["id"]: item["library_root"] for item in items}
        assert by_id[root] is True
        assert by_id[leaf] is False
