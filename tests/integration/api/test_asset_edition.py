"""Integration tests for the edition field on Asset (issue #92).

The field decides whether a detail screen plays or asks: siblings differing only in
encoding may be chosen between silently, siblings differing in edition may not. So the
distinction that matters at the API boundary is between null and a value, and the tests
below are mostly about keeping those two apart through the write paths.

The parser has its own unit tests; nothing here calls it. That separation is the point of
#92 -- the parser is an ingest-time tool, and no request path may depend on it.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from tests.factories import AssetReadFactory, get_asset_creation_json


def _payload(**overrides) -> dict:
    body = get_asset_creation_json(AssetReadFactory())
    body.update(overrides)
    return body


@pytest.mark.integration
class TestEditionOnWrite:

    def test_defaults_to_null(self, client: TestClient):
        """Null means no edition is recorded, which is what licenses a silent choice."""
        response = client.post("/api/assets/", json=_payload())

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["edition"] is None

    def test_can_be_set_on_create(self, client: TestClient):
        response = client.post("/api/assets/", json=_payload(edition="directors_cut"))

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["edition"] == "directors_cut"

    def test_accepts_a_value_outside_the_canonical_vocabulary(self, client: TestClient):
        """An edition this codebase has not heard of is still an edition. Rejecting it
        would push the caller towards null, which asserts the opposite of the truth."""
        response = client.post("/api/assets/", json=_payload(edition="imax_enhanced"))

        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["edition"] == "imax_enhanced"

    def test_can_be_set_by_patch(self, client: TestClient):
        asset_id = client.post("/api/assets/", json=_payload()).json()["id"]

        response = client.patch(f"/api/assets/{asset_id}", json={"edition": "extended"})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["edition"] == "extended"

    def test_a_patch_that_omits_it_leaves_it_alone(self, client: TestClient):
        asset_id = client.post("/api/assets/", json=_payload(edition="unrated")).json()["id"]

        response = client.patch(f"/api/assets/{asset_id}", json={"filename": "renamed.mkv"})

        assert response.status_code == HTTPStatus.OK
        assert response.json()["edition"] == "unrated"


@pytest.mark.integration
class TestEditionOnRead:

    def test_exposed_on_the_detail_route(self, client: TestClient):
        asset_id = client.post("/api/assets/", json=_payload(edition="final_cut")).json()["id"]

        assert client.get(f"/api/assets/{asset_id}").json()["edition"] == "final_cut"

    def test_exposed_on_the_list_route(self, client: TestClient):
        """The play-or-ask decision is made over a title's siblings, which arrive as a
        list, so the field has to survive the list serialiser."""
        cut = client.post("/api/assets/", json=_payload(edition="directors_cut")).json()["id"]
        plain = client.post("/api/assets/", json=_payload()).json()["id"]

        items = client.get("/api/assets/?limit=500").json()["items"]

        by_id = {item["id"]: item["edition"] for item in items}
        assert by_id[cut] == "directors_cut"
        assert by_id[plain] is None
