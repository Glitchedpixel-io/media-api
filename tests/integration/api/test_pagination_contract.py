"""The cursor contract shared by every paginated endpoint.

`PageInfo.next` is documented as "Opaque cursor for the next page, or null if this is
the last page", and that is the contract clients write `while (next)` loops against.
Before #66 it was never null — sqlakeyset produces a marker for "everything after the
last row I returned" unconditionally — so the obvious loop ran forever, fetching empty
pages. On an empty collection it span on the very first request.

These tests walk each endpoint exactly the way a client would, with no defensive
stopping conditions, so a regression hangs nothing: the safety cap fails the test.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import MediaRepository, StreamRepository
from app.schemas import AssetCreateInternal, StreamCreateInternal
from tests.factories import AssetReadFactory, StreamReadFactory

# Every endpoint returning PaginatedResponse. Each goes through the same
# SQLAlchemyBaseRepository._page_info, so a regression in one is a regression in all.
PAGINATED_PATHS = [
    "/api/assets/",
    "/api/titles/",
    "/api/tags",
    "/api/transform_requests",
    "/api/streams",
]

# A client following cursors cannot know how many pages there are, so a runaway loop is
# bounded only by this. Any real case here needs a handful of pages at most.
MAX_PAGES = 25


def _walk_naively(client: TestClient, path: str, limit: int) -> list[int]:
    """Follow `page.next` until it is null — the loop the field's description invites.

    Deliberately has no empty-page or stalled-cursor check. Those are workarounds for
    the bug this module exists to prevent, and including them here would mean the test
    passes whether or not the contract holds.

    Args:
        client: Test client.
        path: Endpoint path, without query string.
        limit: Page size to request.

    Returns:
        list[int]: The id of every row seen, in the order the cursors returned them.

    Raises:
        AssertionError: If the cursor does not terminate within MAX_PAGES.
    """
    seen: list[int] = []
    joiner = "&" if "?" in path else "?"
    url = f"{path}{joiner}limit={limit}"

    for _ in range(MAX_PAGES):
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK, response.text
        body = response.json()
        seen.extend(item["id"] for item in body["items"])

        cursor = body["page"]["next"]
        if cursor is None:
            return seen
        url = f"{path}{joiner}limit={limit}&after={cursor}"

    raise AssertionError(
        f"{path} never returned a null next cursor after {MAX_PAGES} pages — "
        "a client looping on `while (next)` would not terminate"
    )


@pytest.mark.api
@pytest.mark.integration
class TestPaginationCursorContract:
    """`page.next` must be null exactly when there is no further page."""

    def _seed_streams(
        self,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
        count: int,
    ) -> None:
        """Create one asset carrying `count` streams."""
        asset = AssetReadFactory()
        created = media_repository.create(
            AssetCreateInternal(**asset.model_dump(exclude={"id", "created_at", "master_asset_id"}))
        )
        for i in range(count):
            stream = StreamReadFactory(asset_id=created.id, stream_index=i)
            stream_repository.create(StreamCreateInternal(**stream.model_dump(exclude={"id"})))

    @pytest.mark.parametrize("path", PAGINATED_PATHS)
    def test_empty_collection_returns_a_null_cursor(self, client: TestClient, path: str) -> None:
        """The case that span on the very first request: no rows at all."""
        response = client.get(f"{path}?limit=5" if "?" not in path else f"{path}&limit=5")

        assert response.status_code == HTTPStatus.OK
        body = response.json()
        assert body["items"] == []
        assert body["page"]["next"] is None, "an empty collection advertised a next page"
        assert body["page"]["prev"] is None

    @pytest.mark.parametrize("path", PAGINATED_PATHS)
    def test_empty_collection_terminates_a_naive_walk(self, client: TestClient, path: str) -> None:
        """Every paginated endpoint, walked the way a client would."""
        assert _walk_naively(client, path, limit=5) == []

    def test_collection_smaller_than_one_page(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """2 rows, limit 3 — obviously one page, and it used to claim otherwise."""
        self._seed_streams(media_repository, stream_repository, 2)

        body = client.get("/api/streams?limit=3").json()

        assert len(body["items"]) == 2
        assert body["page"]["next"] is None
        assert len(_walk_naively(client, "/api/streams", limit=3)) == 2

    def test_collection_that_is_an_exact_multiple_of_the_page_size(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """6 rows, limit 3 — the classic off-by-one, where the last full page is the end."""
        self._seed_streams(media_repository, stream_repository, 6)

        seen = _walk_naively(client, "/api/streams", limit=3)

        assert len(seen) == 6
        assert len(set(seen)) == 6, "a row was returned on more than one page"

    def test_collection_with_a_partial_final_page(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """7 rows, limit 3 — pages of 3, 3, 1, then done."""
        self._seed_streams(media_repository, stream_repository, 7)

        seen = _walk_naively(client, "/api/streams", limit=3)

        assert len(seen) == 7
        assert len(set(seen)) == 7, "a row was returned on more than one page"

    def test_first_page_has_no_prev_cursor(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """`prev` carries the same promise as `next`, in the other direction."""
        self._seed_streams(media_repository, stream_repository, 6)

        body = client.get("/api/streams?limit=3").json()

        assert body["page"]["prev"] is None, "the first page advertised a previous page"
        assert body["page"]["next"] is not None

    def test_second_page_has_both_cursors(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        stream_repository: StreamRepository,
    ) -> None:
        """A middle page still points both ways — the fix must not over-nullify."""
        self._seed_streams(media_repository, stream_repository, 9)

        first = client.get("/api/streams?limit=3").json()
        second = client.get(f"/api/streams?limit=3&after={first['page']['next']}").json()

        assert len(second["items"]) == 3
        assert second["page"]["next"] is not None
        assert second["page"]["prev"] is not None
