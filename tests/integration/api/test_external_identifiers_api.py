"""
FastAPI Integration Tests for External Identifiers

Covers endpoints:
- /api/external-ids/resolve (resolution endpoint)
- /api/titles/{title_id}/ids (title external IDs CRUD)
- Backward compatibility with /api/assets/{asset_id}/ids

These are full-stack tests that exercise routers, services, repositories, and DB.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.factories import (
    AssetReadFactory,
    TitleReadFactory,
    get_asset_creation_json,
    get_title_creation_json,
)


@pytest.mark.api
@pytest.mark.integration
class TestExternalIdResolutionAPI:
    """Test the external ID resolution endpoint."""

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

    def _create_title(self, client: TestClient) -> dict:
        title_data = TitleReadFactory()
        res = client.post("/api/titles", json=get_title_creation_json(title_data))
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_resolve_asset_external_id(self, client: TestClient) -> None:
        """Test resolving an external ID that points to an asset."""
        # Setup: Create asset, scheme, and external ID
        asset = self._create_asset(client)
        scheme = self._create_scheme(client, code="imdb", label="IMDb")

        # Attach external ID to asset
        ext_id_payload = {"scheme_id": scheme["id"], "external_id": "tt1234567"}
        res = client.post(f"/api/assets/{asset['id']}/ids", json=ext_id_payload)
        assert res.status_code == HTTPStatus.CREATED

        # Test resolution
        res = client.get(
            "/api/external-ids/resolve", params={"scheme": "imdb", "external_id": "tt1234567"}
        )
        assert res.status_code == HTTPStatus.OK

        data = res.json()
        assert data["entity_type"] == "asset"
        assert data["entity_id"] == asset["id"]
        assert data["scheme_code"] == "imdb"
        assert data["external_id"] == "tt1234567"

    def test_resolve_title_external_id(self, client: TestClient) -> None:
        """Test resolving an external ID that points to a title."""
        # Setup: Create title, scheme, and external ID
        title = self._create_title(client)
        scheme = self._create_scheme(client, code="tmdb", label="TMDB")

        # Attach external ID to title
        ext_id_payload = {"scheme_id": scheme["id"], "external_id": "12345"}
        res = client.post(f"/api/titles/{title['id']}/ids", json=ext_id_payload)
        assert res.status_code == HTTPStatus.CREATED

        # Test resolution
        res = client.get(
            "/api/external-ids/resolve", params={"scheme": "tmdb", "external_id": "12345"}
        )
        assert res.status_code == HTTPStatus.OK

        data = res.json()
        assert data["entity_type"] == "title"
        assert data["entity_id"] == title["id"]
        assert data["scheme_code"] == "tmdb"
        assert data["external_id"] == "12345"

    def test_resolve_nonexistent_external_id(self, client: TestClient) -> None:
        """Test that resolving a nonexistent external ID returns 404."""
        self._create_scheme(client, code="imdb", label="IMDb")

        res = client.get(
            "/api/external-ids/resolve", params={"scheme": "imdb", "external_id": "nonexistent"}
        )
        assert res.status_code == HTTPStatus.NOT_FOUND
        assert "not found" in res.json()["detail"].lower()

    def test_resolve_with_nonexistent_scheme(self, client: TestClient) -> None:
        """Test that resolving with a nonexistent scheme returns 404."""
        res = client.get(
            "/api/external-ids/resolve", params={"scheme": "nonexistent", "external_id": "123"}
        )
        assert res.status_code == HTTPStatus.NOT_FOUND

    def test_resolve_same_external_id_different_schemes(self, client: TestClient) -> None:
        """Test that the same external_id value can exist in different schemes."""
        asset = self._create_asset(client)
        title = self._create_title(client)
        scheme1 = self._create_scheme(client, code="scheme1", label="Scheme 1")
        scheme2 = self._create_scheme(client, code="scheme2", label="Scheme 2")

        # Attach "123" to asset in scheme1
        client.post(
            f"/api/assets/{asset['id']}/ids",
            json={"scheme_id": scheme1["id"], "external_id": "123"},
        )

        # Attach "123" to title in scheme2
        client.post(
            f"/api/titles/{title['id']}/ids",
            json={"scheme_id": scheme2["id"], "external_id": "123"},
        )

        # Resolve both
        res1 = client.get(
            "/api/external-ids/resolve", params={"scheme": "scheme1", "external_id": "123"}
        )
        assert res1.status_code == HTTPStatus.OK
        assert res1.json()["entity_type"] == "asset"
        assert res1.json()["entity_id"] == asset["id"]

        res2 = client.get(
            "/api/external-ids/resolve", params={"scheme": "scheme2", "external_id": "123"}
        )
        assert res2.status_code == HTTPStatus.OK
        assert res2.json()["entity_type"] == "title"
        assert res2.json()["entity_id"] == title["id"]


@pytest.mark.api
@pytest.mark.integration
class TestTitleExternalIdsAPI:
    """Test CRUD operations for title external IDs."""

    def _create_scheme(
        self, client: TestClient, code: str, label: str, validator: str | None = None
    ) -> dict:
        payload = {"code": code, "label": label, "validator": validator}
        res = client.post("/api/id_schemes", json=payload)
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def _create_title(self, client: TestClient) -> dict:
        title_data = TitleReadFactory()
        res = client.post("/api/titles", json=get_title_creation_json(title_data))
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_title_external_ids_full_crud_flow(self, client: TestClient) -> None:
        """Test the complete CRUD flow for title external IDs."""
        title = self._create_title(client)
        scheme = self._create_scheme(client, code="tmdb", label="TMDB")
        title_id = title["id"]
        scheme_id = scheme["id"]

        # List - initially empty
        res = client.get(f"/api/titles/{title_id}/ids")
        assert res.status_code == HTTPStatus.OK
        assert res.json() == []

        # Create external ID
        create_payload = {"scheme_id": scheme_id, "external_id": "67890"}
        res = client.post(f"/api/titles/{title_id}/ids", json=create_payload)
        assert res.status_code == HTTPStatus.CREATED
        created = res.json()
        assert created["id"] > 0
        assert created["entity_type"] == "title"
        assert created["entity_id"] == title_id
        assert created["scheme_id"] == scheme_id
        assert created["external_id"] == "67890"
        assert "created_at" in created

        record_id = created["id"]

        # List - should have one entry with scheme details
        res = client.get(f"/api/titles/{title_id}/ids")
        assert res.status_code == HTTPStatus.OK
        items = res.json()
        assert len(items) == 1
        item = items[0]
        assert item["id"] == record_id
        assert item["scheme"] is not None
        assert item["scheme"]["code"] == "tmdb"

        # Update external ID
        res = client.patch(f"/api/titles/{title_id}/ids/{record_id}", json={"external_id": "99999"})
        assert res.status_code == HTTPStatus.OK
        updated = res.json()
        assert updated["id"] == record_id
        assert updated["external_id"] == "99999"

        # Delete external ID
        res = client.delete(f"/api/titles/{title_id}/ids/{record_id}")
        assert res.status_code in (HTTPStatus.OK, HTTPStatus.NO_CONTENT)

        # Confirm deletion
        res = client.get(f"/api/titles/{title_id}/ids")
        assert res.status_code == HTTPStatus.OK
        assert res.json() == []

    def test_create_title_external_id_nonexistent_title(self, client: TestClient) -> None:
        """Test that creating an external ID for a nonexistent title returns 404."""
        scheme = self._create_scheme(client, code="test", label="Test")

        res = client.post(
            "/api/titles/999999/ids", json={"scheme_id": scheme["id"], "external_id": "abc"}
        )
        assert res.status_code == HTTPStatus.NOT_FOUND
        assert "not found" in res.json()["detail"].lower()

    def test_create_title_external_id_nonexistent_scheme(self, client: TestClient) -> None:
        """Test that creating an external ID with a nonexistent scheme returns 422."""
        title = self._create_title(client)

        res = client.post(
            f"/api/titles/{title['id']}/ids", json={"scheme_id": 999999, "external_id": "abc"}
        )
        assert res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_create_title_external_id_duplicate_in_same_scheme(self, client: TestClient) -> None:
        """Test that creating a duplicate external ID in the same scheme returns 409."""
        title1 = self._create_title(client)
        title2 = self._create_title(client)
        scheme = self._create_scheme(client, code="test", label="Test")

        # Create first external ID
        client.post(
            f"/api/titles/{title1['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "duplicate"},
        )

        # Try to create the same external ID for a different title
        res = client.post(
            f"/api/titles/{title2['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "duplicate"},
        )
        assert res.status_code == HTTPStatus.CONFLICT

    def test_create_multiple_external_ids_for_same_title(self, client: TestClient) -> None:
        """Test that a title can have multiple external IDs in different schemes."""
        title = self._create_title(client)
        scheme1 = self._create_scheme(client, code="imdb", label="IMDb")
        scheme2 = self._create_scheme(client, code="tmdb", label="TMDB")

        # Create first external ID
        res1 = client.post(
            f"/api/titles/{title['id']}/ids",
            json={"scheme_id": scheme1["id"], "external_id": "tt123"},
        )
        assert res1.status_code == HTTPStatus.CREATED

        # Create second external ID
        res2 = client.post(
            f"/api/titles/{title['id']}/ids",
            json={"scheme_id": scheme2["id"], "external_id": "456"},
        )
        assert res2.status_code == HTTPStatus.CREATED

        # List should have both
        res = client.get(f"/api/titles/{title['id']}/ids")
        assert res.status_code == HTTPStatus.OK
        items = res.json()
        assert len(items) == 2

    def test_update_title_external_id_wrong_title(self, client: TestClient) -> None:
        """Test that updating an external ID via wrong title ID returns 404."""
        title1 = self._create_title(client)
        title2 = self._create_title(client)
        scheme = self._create_scheme(client, code="test", label="Test")

        # Create external ID for title1
        res = client.post(
            f"/api/titles/{title1['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "abc"},
        )
        record_id = res.json()["id"]

        # Try to update via title2
        res = client.patch(
            f"/api/titles/{title2['id']}/ids/{record_id}", json={"external_id": "xyz"}
        )
        assert res.status_code == HTTPStatus.NOT_FOUND

    def test_delete_title_external_id_wrong_title(self, client: TestClient) -> None:
        """Test that deleting an external ID via wrong title ID returns 404."""
        title1 = self._create_title(client)
        title2 = self._create_title(client)
        scheme = self._create_scheme(client, code="test", label="Test")

        # Create external ID for title1
        res = client.post(
            f"/api/titles/{title1['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "abc"},
        )
        record_id = res.json()["id"]

        # Try to delete via title2
        res = client.delete(f"/api/titles/{title2['id']}/ids/{record_id}")
        assert res.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.api
@pytest.mark.integration
class TestBackwardCompatibilityAssetExternalIds:
    """Test that existing asset external ID endpoints still work with the new table."""

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

    def test_asset_external_ids_still_work(self, client: TestClient) -> None:
        """Test that asset external ID endpoints work with the new backend."""
        asset = self._create_asset(client)
        scheme = self._create_scheme(client, code="imdb", label="IMDb")

        # Create external ID for asset
        res = client.post(
            f"/api/assets/{asset['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "tt999"},
        )
        assert res.status_code == HTTPStatus.CREATED
        created = res.json()
        assert (
            created["entity_id"] == asset["id"] and created["entity_type"] == "asset"
        )  # Should still return asset_id for backward compatibility
        assert created["external_id"] == "tt999"

        # List should work
        res = client.get(f"/api/assets/{asset['id']}/ids")
        assert res.status_code == HTTPStatus.OK
        items = res.json()
        assert len(items) == 1
        assert items[0]["entity_id"] == asset["id"] and items[0]["entity_type"] == "asset"

    def test_asset_external_id_can_be_resolved(self, client: TestClient) -> None:
        """Test that asset external IDs can be resolved via the new resolution endpoint."""
        asset = self._create_asset(client)
        scheme = self._create_scheme(client, code="test", label="Test")

        # Create external ID via old endpoint
        client.post(
            f"/api/assets/{asset['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "xyz123"},
        )

        # Resolve via new endpoint
        res = client.get(
            "/api/external-ids/resolve", params={"scheme": "test", "external_id": "xyz123"}
        )
        assert res.status_code == HTTPStatus.OK
        data = res.json()
        assert data["entity_type"] == "asset"
        assert data["entity_id"] == asset["id"]


@pytest.mark.api
@pytest.mark.integration
class TestCrossEntityExternalIdEnforcement:
    """Test uniqueness enforcement across assets and titles."""

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

    def _create_title(self, client: TestClient) -> dict:
        title_data = TitleReadFactory()
        res = client.post("/api/titles", json=get_title_creation_json(title_data))
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_cannot_assign_same_external_id_to_asset_and_title_in_same_scheme(
        self, client: TestClient
    ) -> None:
        """Test that (scheme, external_id) must be unique across all entities."""
        asset = self._create_asset(client)
        title = self._create_title(client)
        scheme = self._create_scheme(client, code="test", label="Test")

        # Assign external ID to asset
        res = client.post(
            f"/api/assets/{asset['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "shared"},
        )
        assert res.status_code == HTTPStatus.CREATED

        # Try to assign same external ID to title - should fail
        res = client.post(
            f"/api/titles/{title['id']}/ids",
            json={"scheme_id": scheme["id"], "external_id": "shared"},
        )
        assert res.status_code == HTTPStatus.CONFLICT

    def test_can_assign_same_external_id_to_asset_and_title_in_different_schemes(
        self, client: TestClient
    ) -> None:
        """Test that the same external_id value can be used in different schemes."""
        asset = self._create_asset(client)
        title = self._create_title(client)
        scheme1 = self._create_scheme(client, code="scheme1", label="Scheme 1")
        scheme2 = self._create_scheme(client, code="scheme2", label="Scheme 2")

        # Assign "123" to asset in scheme1
        res = client.post(
            f"/api/assets/{asset['id']}/ids",
            json={"scheme_id": scheme1["id"], "external_id": "123"},
        )
        assert res.status_code == HTTPStatus.CREATED

        # Assign "123" to title in scheme2 - should succeed
        res = client.post(
            f"/api/titles/{title['id']}/ids",
            json={"scheme_id": scheme2["id"], "external_id": "123"},
        )
        assert res.status_code == HTTPStatus.CREATED


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


@pytest.mark.api
@pytest.mark.integration
class TestTitleIdsQueryCount:
    """Guard the cost of GET /api/titles/{id}/ids against an N+1 (#95).

    ``ExternalIdentifierReadExtended`` serialises ``scheme``, so if the relationship
    lazy-loads the endpoint issues one extra SELECT against id_schemes per row. That
    is invisible to a correctness test -- the response body is identical either way --
    so it is asserted here as a query count, the same shape as #49.
    """

    def _create_scheme(self, client: TestClient, code: str, label: str) -> dict:
        res = client.post("/api/id_schemes", json={"code": code, "label": label, "validator": None})
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def _create_title(self, client: TestClient) -> dict:
        res = client.post("/api/titles", json=get_title_creation_json(TitleReadFactory()))
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_scheme_queries_do_not_scale_with_identifiers_returned(
        self, client: TestClient, _test_engine: Engine
    ) -> None:
        """A title with more identifiers must not cost more queries against id_schemes.

        Each identifier gets its own scheme deliberately. Pointing them all at one
        shared scheme makes this test vacuous: the first lazy load puts that scheme in
        the session identity map and every later row is served from it, so an
        unfixed N+1 still shows a flat query count and the test passes green.
        """
        # Arrange - two titles, one carrying two identifiers and one carrying six,
        # every identifier in a distinct scheme so a lazy load costs a real SELECT
        counts: dict[int, int] = {}
        for n in (2, 6):
            title = self._create_title(client)
            for i in range(n):
                scheme = self._create_scheme(client, code=f"s{n}_{i}", label=f"Scheme {n}-{i}")
                res = client.post(
                    f"/api/titles/{title['id']}/ids",
                    json={"scheme_id": scheme["id"], "external_id": f"tt{n}{i:06d}"},
                )
                assert res.status_code == HTTPStatus.CREATED, res.text

            # Act - read it back, counting only the statements touching id_schemes
            with _statements_touching(_test_engine, "id_schemes") as statements:
                response = client.get(f"/api/titles/{title['id']}/ids")
            assert response.status_code == HTTPStatus.OK
            body = response.json()
            assert len(body) == n
            # The field must actually be populated, or the count below proves nothing
            assert all(item["scheme"] for item in body)
            counts[n] = len(statements)

        # Assert - cost is a property of the request, not of the row count
        assert counts[2] == counts[6], (
            f"id_schemes queries scaled with the number of identifiers: "
            f"{counts[2]} for 2 rows, {counts[6]} for 6 rows"
        )
        assert counts[6] < 6, f"expected a bounded number of queries, got {counts[6]} for 6 rows"
