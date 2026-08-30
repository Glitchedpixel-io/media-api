"""Integration tests for the tag_ids filter on GET /api/assets/ (issue #132).

`?tag_ids=abc` reached `int()` unguarded in the repository and was served as a 500 --
a caller-caused condition arriving as a server fault, which `QuietClientErrorRoute`
cannot quiet because it only converts a status that is already correct. So the wrong
status was not only wrong for the caller, it also opened a Logfire issue for every
malformed request.

The status is what these assert. A test that only checked "does not raise" would have
passed against the broken code, since the 500 was itself a handled response by the time
it reached the client.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories import SQLAlchemyMediaRepository, SQLAlchemyTagRepository
from app.schemas import AssetCreateInternal, TagCreateInternal


@pytest.fixture
def make_asset(db_session):
    repo = SQLAlchemyMediaRepository(db_session)
    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        n = counter["n"]
        return repo.create(
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

    return _make


@pytest.fixture
def make_tag(db_session):
    repo = SQLAlchemyTagRepository(db_session)
    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        return repo.create(TagCreateInternal(name=f"tag-{counter['n']}")).id

    return _make


@pytest.fixture
def tag_asset(db_session):
    from app.models import AssetTagORM

    def _tag(asset_id: int, tag_id: int) -> None:
        db_session.add(AssetTagORM(asset_id=asset_id, tag_id=tag_id))
        db_session.commit()

    return _tag


def _ids(client: TestClient, query: str) -> set[int]:
    response = client.get(f"/api/assets/?{query}&limit=500")
    assert response.status_code == HTTPStatus.OK, response.text
    return {item["id"] for item in response.json()["items"]}


@pytest.mark.api
@pytest.mark.integration
class TestAssetTagFilterRejectsNonNumeric:
    """The regression #132 records: a bad tag id is a 422, not a 500."""

    @pytest.mark.parametrize("value", ["abc", "1,abc", "abc,1", "1.5", "-", "1 2"])
    def test_a_non_numeric_tag_id_is_a_422(self, client: TestClient, value: str) -> None:
        response = client.get("/api/assets/", params={"tag_ids": value})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text

    def test_the_422_body_matches_the_shape_every_other_rejection_uses(
        self, client: TestClient
    ) -> None:
        """`domain_error_detail` exists so a client need not branch on whether `detail`
        is a string or a list depending on which layer rejected the request."""
        response = client.get("/api/assets/", params={"tag_ids": "abc"})

        detail = response.json()["detail"]
        assert isinstance(detail, list)
        assert {"loc", "msg", "type"} <= set(detail[0])

    def test_it_matches_the_status_the_title_listing_already_returned(
        self, client: TestClient
    ) -> None:
        """The same malformed parameter on the sibling endpoint, fixed in #131. These
        two parse the same value and disagreeing on the status would be its own bug."""
        assets = client.get("/api/assets/", params={"tag_ids": "abc"})
        titles = client.get("/api/titles/", params={"tag_ids": "abc"})

        assert assets.status_code == titles.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.api
@pytest.mark.integration
class TestAssetTagFilterStillFilters:
    """The guard must not have changed what a well-formed request returns."""

    def test_matches_any_of_several_tags(
        self, client: TestClient, make_asset, make_tag, tag_asset
    ) -> None:
        a, b, c = make_tag(), make_tag(), make_tag()
        first, second, other = make_asset(), make_asset(), make_asset()
        tag_asset(first, a)
        tag_asset(second, b)
        tag_asset(other, c)

        assert _ids(client, f"tag_ids={a},{b}") == {first, second}

    def test_an_asset_with_two_matching_tags_appears_once(
        self, client: TestClient, make_asset, make_tag, tag_asset
    ) -> None:
        """What the DISTINCT in the repository is for: otherwise `limit` stops being a
        cap on assets and the keyset cursor is computed over duplicated rows."""
        a, b = make_tag(), make_tag()
        asset = make_asset()
        tag_asset(asset, a)
        tag_asset(asset, b)

        items = client.get(f"/api/assets/?tag_ids={a},{b}&limit=500").json()["items"]

        assert [item["id"] for item in items] == [asset]

    def test_empty_segments_are_ignored_rather_than_rejected(
        self, client: TestClient, make_asset, make_tag, tag_asset
    ) -> None:
        """A trailing comma is what a UI building the list by concatenation emits, and
        it was tolerated before the guard. It still is -- only a non-numeric segment is
        an error."""
        a = make_tag()
        asset = make_asset()
        tag_asset(asset, a)

        assert _ids(client, f"tag_ids={a},") == {asset}

    def test_a_whitespace_only_segment_is_an_empty_segment(
        self, client: TestClient, make_asset, make_tag, tag_asset
    ) -> None:
        """It strips to nothing, so it is dropped like a trailing comma rather than
        reaching `int()`. Only a segment with actual non-numeric content is an error."""
        a = make_tag()
        asset = make_asset()
        tag_asset(asset, a)

        assert _ids(client, f"tag_ids={a}, ") == {asset}

    def test_an_unknown_tag_id_matches_nothing(
        self, client: TestClient, make_asset, make_tag, tag_asset
    ) -> None:
        """Well-formed but absent is an empty page, not a 422 -- the same call the grid
        makes with a stale tag list."""
        a = make_tag()
        asset = make_asset()
        tag_asset(asset, a)

        assert _ids(client, "tag_ids=987654") == set()
