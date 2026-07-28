"""
FastAPI Integration Tests for ID Schemes and Asset External IDs

Covers endpoints:
- /api/id_schemes (list, get, create, patch)
- /api/assets/{asset_id}/ids (list, create, patch, delete)

These are full-stack tests that exercise routers, services, repositories, and DB.
"""

from __future__ import annotations

from http import HTTPStatus
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.factories import AssetReadFactory, get_asset_creation_json


@pytest.mark.api
@pytest.mark.integration
class TestIdSchemesAPI:
    def _create_scheme(
        self, client: TestClient, code: str, label: str, validator: str | None = None
    ) -> dict:
        payload = {"code": code, "label": label, "validator": validator}
        res = client.post("/api/id_schemes", json=payload)
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def _create_asset(self, client: TestClient) -> dict:
        asset = AssetReadFactory()
        res = client.post("/api/assets", json=get_asset_creation_json(asset))  # type: ignore[arg-type]
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_id_schemes_crud_flow(self, client: TestClient) -> None:
        # Create
        created = self._create_scheme(client, code="imdb", label="IMDb")
        assert created["id"] > 0
        assert created["code"] == "imdb"
        assert created["label"] == "IMDb"
        assert created["validator"] is None

        scheme_id = created["id"]

        # List
        res = client.get("/api/id_schemes")
        assert res.status_code == HTTPStatus.OK
        items = res.json()
        assert isinstance(items, list)
        assert any(it["id"] == scheme_id for it in items)

        # Get by id
        res = client.get(f"/api/id_schemes/{scheme_id}")
        assert res.status_code == HTTPStatus.OK
        assert res.json()["id"] == scheme_id

        # Patch (update label and validator)
        patch = {"label": "Internet Movie Database", "validator": None}
        res = client.patch(f"/api/id_schemes/{scheme_id}", json=patch)
        assert res.status_code == HTTPStatus.OK
        body = res.json()
        assert body["id"] == scheme_id
        assert body["label"] == "Internet Movie Database"
        assert body["code"] == "imdb"  # unchanged

    def test_create_id_scheme_conflict(self, client: TestClient) -> None:
        # First create succeeds
        self._create_scheme(client, code="tmdb", label="TMDB")
        # Second create with same code should violate unique constraint
        res = client.post("/api/id_schemes", json={"code": "tmdb", "label": "TMDB"})
        assert res.status_code == HTTPStatus.CONFLICT

    def test_get_unknown_scheme(self, client: TestClient) -> None:
        res = client.get("/api/id_schemes/999999")
        assert res.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.api
@pytest.mark.integration
class TestAssetExternalIdsAPI:
    def _bootstrap_asset_and_scheme(self, client: TestClient) -> tuple[dict, dict]:
        asset = AssetReadFactory()
        asset_res = client.post("/api/assets", json=get_asset_creation_json(asset))  # type: ignore[arg-type]
        assert asset_res.status_code == HTTPStatus.CREATED, asset_res.text
        created_asset = asset_res.json()

        scheme = {"code": "yt", "label": "YouTube"}
        scheme_res = client.post("/api/id_schemes", json=scheme)
        assert scheme_res.status_code == HTTPStatus.CREATED, scheme_res.text
        created_scheme = scheme_res.json()
        return created_asset, created_scheme

    def test_external_ids_full_flow(self, client: TestClient, db_session: Session) -> None:
        asset, scheme = self._bootstrap_asset_and_scheme(client)
        asset_id = asset["id"]
        scheme_id = scheme["id"]

        # Initially empty list
        res = client.get(f"/api/assets/{asset_id}/ids")
        assert res.status_code == HTTPStatus.OK
        assert res.json() == []

        # Create an external id for the asset
        create_payload = {"scheme_id": scheme_id, "external_id": "abcd1234"}
        res = client.post(f"/api/assets/{asset_id}/ids", json=create_payload)
        assert res.status_code == HTTPStatus.CREATED, res.text
        created_ref = res.json()

        assert created_ref["id"] > 0
        assert created_ref["entity_id"] == asset_id
        assert created_ref["entity_type"] == "asset"
        assert created_ref["scheme_id"] == scheme_id
        assert created_ref["external_id"] == "abcd1234"

        record_id = created_ref["id"]

        # List should include extended scheme info
        res = client.get(f"/api/assets/{asset_id}/ids")
        assert res.status_code == HTTPStatus.OK
        items = res.json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == record_id
        assert item["scheme"] is not None
        assert item["scheme"]["id"] == scheme_id
        assert item["scheme"]["code"] == "yt"

        # Patch external id value
        res = client.patch(
            f"/api/assets/{asset_id}/ids/{record_id}", json={"external_id": "efgh5678"}
        )
        assert res.status_code == HTTPStatus.OK
        updated = res.json()
        assert updated["id"] == record_id
        assert updated["external_id"] == "efgh5678"

        # Delete the external id
        res = client.delete(f"/api/assets/{asset_id}/ids/{record_id}")
        assert res.status_code in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)

        # Confirm it's gone
        res = client.get(f"/api/assets/{asset_id}/ids")
        assert res.status_code == HTTPStatus.OK
        assert res.json() == []

    def test_external_id_invalid_scheme_fk(self, client: TestClient) -> None:
        # Create an asset
        asset = AssetReadFactory()
        res = client.post("/api/assets", json=get_asset_creation_json(asset))  # type: ignore[arg-type]
        assert res.status_code == HTTPStatus.CREATED, res.text
        asset_id = res.json()["id"]

        # Try to attach external id with non-existent scheme id → FK violation mapped to 422
        res = client.post(
            f"/api/assets/{asset_id}/ids",
            json={"scheme_id": 999999, "external_id": "nope"},
        )
        assert res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_external_id_404s_on_wrong_asset(self, client: TestClient) -> None:
        # Create asset and scheme and one id
        asset, scheme = self._bootstrap_asset_and_scheme(client)
        a_id = asset["id"]
        s_id = scheme["id"]
        res = client.post(
            f"/api/assets/{a_id}/ids",
            json={"scheme_id": s_id, "external_id": "X1"},
        )
        assert res.status_code == HTTPStatus.CREATED
        rec_id = res.json()["id"]

        # Update via wrong asset id → 404
        res = client.patch(f"/api/assets/999999/ids/{rec_id}", json={"external_id": "X2"})
        assert res.status_code == HTTPStatus.NOT_FOUND

        # Delete via wrong asset id → 404
        res = client.delete(f"/api/assets/999999/ids/{rec_id}")
        assert res.status_code == HTTPStatus.NOT_FOUND
