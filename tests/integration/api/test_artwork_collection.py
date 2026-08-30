"""Integration tests for the artwork collection route (issue #113).

Before this route, artwork was reachable only through an id the caller already held:
no way to ask what artwork exists, and no way to walk it. What matters here is that the
filters compose, that the page is capped like every other listing rather than inheriting
the uncapped shape of the nested routes, and that an unknown kind is refused rather than
silently answered with an empty page.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories import SQLAlchemyArtworkRepository, SQLAlchemyMediaRepository
from app.repositories.protocols import TitleRepository
from app.schemas import ArtworkCreateInternal, AssetCreateInternal, TitleCreateInternal
from app.schemas.enums import EntityTypeEnum

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.fixture
def make_artwork(db_session, artwork_kind_ids: dict[str, int]):
    """Register an artwork row directly, so a test can shape the collection it needs."""
    artwork_repo = SQLAlchemyArtworkRepository(db_session)
    media_repo = SQLAlchemyMediaRepository(db_session)
    counter = {"n": 0}

    def _make(
        *,
        entity_type: EntityTypeEnum = EntityTypeEnum.asset,
        entity_id: int | None = None,
        kind: str = "poster",
        is_primary: bool = True,
        width: int = 640,
        height: int = 960,
    ) -> int:
        counter["n"] += 1
        n = counter["n"]
        if entity_id is None:
            entity_id = media_repo.create(
                AssetCreateInternal(
                    path=f"movies/{n}.mkv",
                    filename=f"{n}.mkv",
                    duration=1.0,
                    bitrate=1,
                    container_format="matroska",
                    size=1,
                    mtime=None,
                    last_seen=None,
                    master_asset_id=None,
                )
            ).id
        return artwork_repo.create(
            ArtworkCreateInternal(
                entity_type=entity_type,
                entity_id=entity_id,
                artwork_kind_id=artwork_kind_ids[kind],
                storage_path=f"ab/cd/{n:064x}.jpg",
                mime="image/jpeg",
                width=width,
                height=height,
                is_primary=is_primary,
                source_scheme_id=None,
                source_external_id=None,
                source_url=None,
            )
        ).id

    return _make


@pytest.mark.integration
class TestListing:

    def test_returns_every_artwork_with_page_cursors(self, client: TestClient, make_artwork):
        for _ in range(3):
            make_artwork()

        body = client.get("/api/artwork").json()

        assert len(body["items"]) == 3
        assert "next" in body["page"]

    def test_an_empty_collection_is_an_empty_page_not_an_error(self, client: TestClient):
        response = client.get("/api/artwork")
        assert response.status_code == HTTPStatus.OK
        assert response.json()["items"] == []

    def test_the_page_is_capped(self, client: TestClient):
        """The nested per-entity routes are uncapped; this one must not copy that."""
        assert client.get("/api/artwork", params={"limit": 501}).status_code == (
            HTTPStatus.UNPROCESSABLE_ENTITY
        )

    def test_paging_walks_the_whole_collection_without_repeats(
        self, client: TestClient, make_artwork
    ):
        made = {make_artwork() for _ in range(5)}

        seen: set[int] = set()
        cursor = None
        for _ in range(5):
            params = {"limit": 2}
            if cursor:
                params["after"] = cursor
            body = client.get("/api/artwork", params=params).json()
            seen.update(item["id"] for item in body["items"])
            cursor = body["page"]["next"]
            if not cursor:
                break

        assert seen == made


@pytest.mark.integration
class TestFilters:

    def test_filters_by_entity_type(
        self, client: TestClient, make_artwork, title_repository: TitleRepository, title_type_ids
    ):
        title_id = title_repository.create(
            TitleCreateInternal(name="A Title", title_type_id=title_type_ids["movie"])
        ).id
        make_artwork()
        make_artwork(entity_type=EntityTypeEnum.title, entity_id=title_id)

        body = client.get("/api/artwork", params={"entity_type": "title"}).json()

        assert len(body["items"]) == 1
        assert body["items"][0]["entity_type"] == "title"

    def test_filters_by_kind_code(self, client: TestClient, make_artwork):
        make_artwork(kind="poster")
        make_artwork(kind="backdrop")

        body = client.get("/api/artwork", params={"kind": "backdrop"}).json()

        assert len(body["items"]) == 1
        assert body["items"][0]["artwork_kind"] == "backdrop"

    def test_an_unknown_kind_is_refused_not_answered_empty(self, client: TestClient, make_artwork):
        """A typo and a kind nothing uses must not look the same."""
        make_artwork()
        response = client.get("/api/artwork", params={"kind": "not-a-kind"})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_filters_by_is_primary(self, client: TestClient, make_artwork):
        asset_artwork = make_artwork(is_primary=True)
        make_artwork(is_primary=False)

        body = client.get("/api/artwork", params={"is_primary": "true"}).json()

        assert [item["id"] for item in body["items"]] == [asset_artwork]

    def test_filters_compose(self, client: TestClient, make_artwork):
        wanted = make_artwork(kind="poster", is_primary=True)
        make_artwork(kind="poster", is_primary=False)
        make_artwork(kind="backdrop", is_primary=True)

        body = client.get("/api/artwork", params={"kind": "poster", "is_primary": "true"}).json()

        assert [item["id"] for item in body["items"]] == [wanted]

    def test_missing_dimensions_is_no_longer_honoured(self, client: TestClient, make_artwork):
        """The filter existed to make the #115 backfill expressible. Since #143 there
        are no unmeasured rows for it to find, so it is gone.

        Worth pinning the *shape* of its removal rather than just its absence: query
        params reach the endpoint through `Depends()`, where FastAPI ignores what the
        model does not declare, so `extra="forbid"` does not produce a 422 here. A
        caller still sending it gets an unfiltered list rather than an error -- which
        is the thing a consumer needs to be told, and is why this ships as a major.
        """
        make_artwork()
        make_artwork()

        body = client.get("/api/artwork", params={"missing_dimensions": "true"}).json()

        assert len(body["items"]) == 2

    def test_an_unsupported_sort_field_is_422_not_500(self, client: TestClient):
        response = client.get("/api/artwork", params={"sort": "storage_path:asc"})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
