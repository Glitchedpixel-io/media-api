"""
FastAPI Integration Tests for Title Types

Covers /api/title_types (list, get, create, patch, delete) and the interaction
between a title type and the titles that reference it.

These are full-stack tests that exercise routers, services, repositories, and DB.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.models import DEFAULT_TITLE_TYPES


@pytest.mark.api
@pytest.mark.integration
class TestTitleTypesAPI:
    def _create_type(self, client: TestClient, code: str, label: str) -> dict:
        res = client.post("/api/title_types", json={"code": code, "label": label})
        assert res.status_code == HTTPStatus.CREATED, res.text
        return res.json()

    def test_list_returns_the_seeded_types_ordered_by_code(self, client: TestClient) -> None:
        res = client.get("/api/title_types")
        assert res.status_code == HTTPStatus.OK
        body = res.json()

        codes = [t["code"] for t in body]
        assert codes == sorted(codes)
        assert set(codes) == {code for code, _ in DEFAULT_TITLE_TYPES}

    def test_crud_flow(self, client: TestClient) -> None:
        created = self._create_type(client, code="podcast", label="Podcast")
        assert created["id"] > 0
        assert created["code"] == "podcast"
        assert created["description"] is None
        type_id = created["id"]

        res = client.get(f"/api/title_types/{type_id}")
        assert res.status_code == HTTPStatus.OK
        assert res.json()["label"] == "Podcast"

        res = client.patch(f"/api/title_types/{type_id}", json={"label": "Podcast Series"})
        assert res.status_code == HTTPStatus.OK
        assert res.json()["label"] == "Podcast Series"
        assert res.json()["code"] == "podcast"

        res = client.delete(f"/api/title_types/{type_id}")
        assert res.status_code == HTTPStatus.NO_CONTENT

        res = client.get(f"/api/title_types/{type_id}")
        assert res.status_code == HTTPStatus.NOT_FOUND

    def test_get_missing_returns_404(self, client: TestClient) -> None:
        assert client.get("/api/title_types/9999").status_code == HTTPStatus.NOT_FOUND

    def test_duplicate_code_returns_409(self, client: TestClient) -> None:
        res = client.post("/api/title_types", json={"code": "movie", "label": "Movie"})
        assert res.status_code == HTTPStatus.CONFLICT

    def test_code_is_normalised_to_lowercase(self, client: TestClient) -> None:
        created = self._create_type(client, code="AudioDrama", label="Audio Drama")
        assert created["code"] == "audiodrama"

    def test_a_new_type_can_immediately_be_used_by_a_title(self, client: TestClient) -> None:
        """The whole point of issue #41: adding a type takes no migration."""
        self._create_type(client, code="podcast", label="Podcast")

        res = client.post("/api/titles", json={"name": "A Podcast", "title_type": "podcast"})
        assert res.status_code == HTTPStatus.CREATED, res.text
        assert res.json()["title_type"] == "podcast"

    def test_deleting_a_type_in_use_returns_409(self, client: TestClient) -> None:
        """A referenced type is a conflict, not a validation error.

        The RESTRICT foreign key alone would surface as a 422; the service's
        usage check is what makes this a 409 with a message naming the count.
        """
        created = self._create_type(client, code="podcast", label="Podcast")

        res = client.post("/api/titles", json={"name": "A Podcast", "title_type": "podcast"})
        assert res.status_code == HTTPStatus.CREATED

        res = client.delete(f"/api/title_types/{created['id']}")
        assert res.status_code == HTTPStatus.CONFLICT
        assert "1" in res.json()["detail"]

        # ...and the type is still there.
        assert client.get(f"/api/title_types/{created['id']}").status_code == HTTPStatus.OK

    def test_deleting_a_type_becomes_possible_once_unused(self, client: TestClient) -> None:
        created = self._create_type(client, code="podcast", label="Podcast")
        res = client.post("/api/titles", json={"name": "A Podcast", "title_type": "podcast"})
        title_id = res.json()["id"]

        assert client.delete(f"/api/title_types/{created['id']}").status_code == (
            HTTPStatus.CONFLICT
        )

        # Reassign the title, then the type is free.
        res = client.patch(f"/api/titles/{title_id}", json={"title_type": "other"})
        assert res.status_code == HTTPStatus.OK
        assert res.json()["title_type"] == "other"

        assert client.delete(f"/api/title_types/{created['id']}").status_code == (
            HTTPStatus.NO_CONTENT
        )


@pytest.mark.api
@pytest.mark.integration
class TestTitleTypeValidationOnTitles:
    def test_unknown_title_type_on_create_is_422(self, client: TestClient) -> None:
        """Unchanged from when title_type was a Postgres enum."""
        res = client.post("/api/titles", json={"name": "X", "title_type": "not_a_type"})
        assert res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "not_a_type" in res.text

    def test_unknown_title_type_on_patch_is_422(self, client: TestClient) -> None:
        res = client.post("/api/titles", json={"name": "X", "title_type": "movie"})
        title_id = res.json()["id"]

        res = client.patch(f"/api/titles/{title_id}", json={"title_type": "not_a_type"})
        assert res.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_patching_another_field_preserves_the_title_type(self, client: TestClient) -> None:
        res = client.post("/api/titles", json={"name": "X", "title_type": "season"})
        title_id = res.json()["id"]

        res = client.patch(f"/api/titles/{title_id}", json={"name": "Renamed"})
        assert res.status_code == HTTPStatus.OK
        assert res.json()["name"] == "Renamed"
        assert res.json()["title_type"] == "season"
