# tests/integration/api/test_title_contents_batch.py

"""Integration tests for the batch containment writes (issue #179).

Placing a directory of media is the gesture the whole placement workflow is built on:
11,945 assets have no intrinsic home, spread over 1,966 directories whose median size is
14 and whose largest holds 796. One request per edge makes that 796 round trips, with no
atomicity and a partial-failure state the interface has to render.

Three properties are worth pinning, and they are what these tests are organised around:

1. **All-or-nothing.** If any item is invalid nothing is written -- following #52, which
   fixed the opposite choice for by-name tagging: committing once per item left an
   arbitrary prefix written and no way for the caller to tell which.
2. **Every failure is reported, not the first.** A caller placing 156 files wants one
   response naming the three bad ones.
3. **Intra-batch validity.** Two items can each be valid alone and invalid together --
   the same asset twice under one parent -- and nothing in the single-write path has
   ever needed to notice that.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import MediaRepository, TitleRepository
from app.schemas import AssetCreateInternal
from app.schemas.title_contents import MAX_BATCH_ITEMS
from tests.factories import AssetReadFactory, TitleReadFactory, get_title_internal


def _title(title_repository: TitleRepository) -> int:
    return title_repository.create(get_title_internal(TitleReadFactory())).id


def _assets(media_repository: MediaRepository, n: int) -> list[int]:
    return [
        media_repository.create(
            AssetCreateInternal(
                **AssetReadFactory().model_dump(exclude={"id", "created_at", "master_asset_id"})
            )
        ).id
        for _ in range(n)
    ]


def _contents(client: TestClient, parent: int) -> list[dict]:
    response = client.get(f"/api/titles/{parent}/contents")
    assert response.status_code == HTTPStatus.OK, response.text
    return response.json()


def _asset_items(asset_ids: list[int]) -> list[dict]:
    return [{"kind": "asset", "asset_id": asset_id} for asset_id in asset_ids]


def _codes(response) -> list[tuple[list, str]]:
    """`(loc, type)` per reported problem, which is what a UI keys on."""
    return [(item["loc"], item["type"]) for item in response.json()["detail"]]


@pytest.mark.api
@pytest.mark.integration
class TestBatchAttach:
    def test_a_directory_lands_in_one_request(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """The median folder, in one call instead of fourteen."""
        parent = _title(title_repository)
        asset_ids = _assets(media_repository, 14)

        response = client.post(
            f"/api/titles/{parent}/contents/batch", json={"items": _asset_items(asset_ids)}
        )

        assert response.status_code == HTTPStatus.CREATED, response.text
        body = response.json()
        assert body["count"] == 14
        assert [item["asset_id"] for item in body["items"]] == asset_ids
        rows = _contents(client, parent)
        assert [row["position"] for row in rows] == list(range(14))

    def test_it_appends_after_what_is_already_there(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)
        first = _assets(media_repository, 2)
        client.post(f"/api/titles/{parent}/contents/batch", json={"items": _asset_items(first)})
        second = _assets(media_repository, 3)

        response = client.post(
            f"/api/titles/{parent}/contents/batch", json={"items": _asset_items(second)}
        )

        assert response.status_code == HTTPStatus.CREATED, response.text
        rows = _contents(client, parent)
        assert [row["asset_id"] for row in rows] == first + second
        assert [row["position"] for row in rows] == list(range(5))

    def test_one_bad_item_writes_nothing(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """All-or-nothing. The property #52 exists to have taught us."""
        parent = _title(title_repository)
        asset_ids = _assets(media_repository, 5)
        items = _asset_items(asset_ids)
        items[2] = {"kind": "asset", "asset_id": 2**31 - 1}

        response = client.post(f"/api/titles/{parent}/contents/batch", json={"items": items})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
        assert _codes(response) == [(["items", 2], "target_missing")]
        assert _contents(client, parent) == []

    def test_every_bad_item_is_named_not_just_the_first(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)
        items = _asset_items(_assets(media_repository, 6))
        items[1] = {"kind": "asset", "asset_id": 2**31 - 1}
        items[4] = {"kind": "asset", "asset_id": 2**31 - 2}

        response = client.post(f"/api/titles/{parent}/contents/batch", json={"items": items})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert _codes(response) == [
            (["items", 1], "target_missing"),
            (["items", 4], "target_missing"),
        ]

    def test_the_same_asset_twice_in_one_batch_is_refused(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """Each item is valid alone. Together they collide on `uq_parent_asset_once`,
        and nothing in the single-write path has ever needed to notice that."""
        parent = _title(title_repository)
        asset_id = _assets(media_repository, 1)[0]

        response = client.post(
            f"/api/titles/{parent}/contents/batch",
            json={"items": _asset_items([asset_id, asset_id])},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
        assert _codes(response) == [(["items", 1], "duplicate_in_batch")]
        assert _contents(client, parent) == []

    def test_an_item_that_would_close_a_cycle_is_a_409_naming_it(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        grandparent = _title(title_repository)
        child = _title(title_repository)
        client.post(
            f"/api/titles/{grandparent}/contents",
            json={"kind": "title", "child_title_id": child},
        )

        items = _asset_items(_assets(media_repository, 2))
        items.append({"kind": "title", "child_title_id": grandparent, "membership": "curated"})

        response = client.post(f"/api/titles/{child}/contents/batch", json={"items": items})

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert _codes(response) == [(["items", 2], "containment_cycle")]
        assert _contents(client, child) == []

    def test_a_second_intrinsic_home_is_a_409_naming_it(
        self, client: TestClient, title_repository
    ) -> None:
        child = _title(title_repository)
        client.post(
            f"/api/titles/{_title(title_repository)}/contents",
            json={"kind": "title", "child_title_id": child},
        )
        other = _title(title_repository)

        response = client.post(
            f"/api/titles/{other}/contents/batch",
            json={"items": [{"kind": "title", "child_title_id": child}]},
        )

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert _codes(response) == [(["items", 0], "intrinsic_parent_conflict")]

    def test_an_unknown_parent_is_404(self, client: TestClient, media_repository) -> None:
        response = client.post(
            f"/api/titles/{2**31 - 1}/contents/batch",
            json={"items": _asset_items(_assets(media_repository, 1))},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.api
@pytest.mark.integration
class TestTheDeclaredCap:
    def test_an_empty_batch_is_refused(self, client: TestClient, title_repository) -> None:
        """`min_length=1`. An empty batch is a caller mistake, not a no-op to absorb."""
        parent = _title(title_repository)

        response = client.post(f"/api/titles/{parent}/contents/batch", json={"items": []})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_over_the_cap_is_refused_by_the_schema(
        self, client: TestClient, title_repository
    ) -> None:
        """Declared in the schema, so it is in the OpenAPI document and refused before
        any work is done -- not discovered by a request that takes a minute to fail."""
        parent = _title(title_repository)
        items = [{"kind": "asset", "asset_id": n + 1} for n in range(MAX_BATCH_ITEMS + 1)]

        response = client.post(f"/api/titles/{parent}/contents/batch", json={"items": items})

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert _contents(client, parent) == []

    def test_the_cap_covers_the_largest_real_directory(self) -> None:
        """796 is the largest unplaced directory in the library; the cap exists so that
        it is one request rather than two."""
        assert MAX_BATCH_ITEMS >= 796


@pytest.mark.api
@pytest.mark.integration
class TestBatchDetach:
    def test_it_removes_them_and_closes_the_gaps(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)
        created = client.post(
            f"/api/titles/{parent}/contents/batch",
            json={"items": _asset_items(_assets(media_repository, 6))},
        ).json()["items"]
        doomed = [created[1]["id"], created[3]["id"]]

        response = client.post(
            f"/api/titles/{parent}/contents/batch/detach", json={"title_contents_ids": doomed}
        )

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["count"] == 2
        rows = _contents(client, parent)
        assert [row["id"] for row in rows] == [
            created[0]["id"],
            created[2]["id"],
            created[4]["id"],
            created[5]["id"],
        ]
        assert [row["position"] for row in rows] == [0, 1, 2, 3]

    def test_an_id_from_another_parent_fails_the_whole_batch(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)
        elsewhere = _title(title_repository)
        mine = client.post(
            f"/api/titles/{parent}/contents/batch",
            json={"items": _asset_items(_assets(media_repository, 3))},
        ).json()["items"]
        theirs = client.post(
            f"/api/titles/{elsewhere}/contents",
            json={"kind": "asset", "asset_id": _assets(media_repository, 1)[0]},
        ).json()

        response = client.post(
            f"/api/titles/{parent}/contents/batch/detach",
            json={"title_contents_ids": [mine[0]["id"], theirs["id"]]},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
        assert _codes(response) == [(["items", 1], "not_under_parent")]
        assert len(_contents(client, parent)) == 3

    def test_repeats_are_collapsed(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """Asking twice for a row to be gone is not a conflicting instruction."""
        parent = _title(title_repository)
        created = client.post(
            f"/api/titles/{parent}/contents/batch",
            json={"items": _asset_items(_assets(media_repository, 2))},
        ).json()["items"]

        response = client.post(
            f"/api/titles/{parent}/contents/batch/detach",
            json={"title_contents_ids": [created[0]["id"], created[0]["id"]]},
        )

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["count"] == 1
        assert len(_contents(client, parent)) == 1


@pytest.mark.api
@pytest.mark.integration
class TestBatchMove:
    def test_entries_from_several_parents_land_in_one(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        destination = _title(title_repository)
        edges = []
        for asset_id in _assets(media_repository, 5):
            source = _title(title_repository)
            edges.append(
                client.post(
                    f"/api/titles/{source}/contents",
                    json={"kind": "asset", "asset_id": asset_id},
                ).json()["id"]
            )

        response = client.post(
            f"/api/titles/{destination}/contents/batch/move",
            json={"title_contents_ids": edges},
        )

        assert response.status_code == HTTPStatus.OK, response.text
        assert response.json()["count"] == 5
        rows = _contents(client, destination)
        assert [row["id"] for row in rows] == edges
        assert [row["position"] for row in rows] == list(range(5))

    def test_the_source_lists_close_their_gaps(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        source = _title(title_repository)
        destination = _title(title_repository)
        created = client.post(
            f"/api/titles/{source}/contents/batch",
            json={"items": _asset_items(_assets(media_repository, 5))},
        ).json()["items"]
        moving = [created[0]["id"], created[2]["id"]]

        response = client.post(
            f"/api/titles/{destination}/contents/batch/move",
            json={"title_contents_ids": moving},
        )

        assert response.status_code == HTTPStatus.OK, response.text
        left_behind = _contents(client, source)
        assert [row["id"] for row in left_behind] == [
            created[1]["id"],
            created[3]["id"],
            created[4]["id"],
        ]
        assert [row["position"] for row in left_behind] == [0, 1, 2]
        assert [row["position"] for row in _contents(client, destination)] == [0, 1]

    def test_a_move_that_would_close_a_cycle_moves_nothing(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        grandparent = _title(title_repository)
        child = _title(title_repository)
        client.post(
            f"/api/titles/{grandparent}/contents",
            json={"kind": "title", "child_title_id": child},
        )
        parked = client.post(
            f"/api/titles/{_title(title_repository)}/contents",
            json={"kind": "title", "child_title_id": grandparent, "membership": "curated"},
        ).json()["id"]
        innocent = client.post(
            f"/api/titles/{_title(title_repository)}/contents",
            json={"kind": "asset", "asset_id": _assets(media_repository, 1)[0]},
        ).json()["id"]

        response = client.post(
            f"/api/titles/{child}/contents/batch/move",
            json={"title_contents_ids": [innocent, parked]},
        )

        assert response.status_code == HTTPStatus.CONFLICT, response.text
        assert _codes(response) == [(["items", 1], "containment_cycle")]
        assert _contents(client, child) == []

    def test_an_unknown_id_fails_the_whole_batch(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        destination = _title(title_repository)
        source = _title(title_repository)
        edge = client.post(
            f"/api/titles/{source}/contents",
            json={"kind": "asset", "asset_id": _assets(media_repository, 1)[0]},
        ).json()["id"]

        response = client.post(
            f"/api/titles/{destination}/contents/batch/move",
            json={"title_contents_ids": [edge, 2**31 - 1]},
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, response.text
        assert _codes(response) == [(["items", 1], "not_found")]
        assert len(_contents(client, source)) == 1
        assert _contents(client, destination) == []


@pytest.mark.api
@pytest.mark.integration
class TestTheBatchRoutesAreReachable:
    """`batch` must not be eaten by `{title_contents_id}`.

    Starlette's default `{param}` regex is `[^/]+`, so `/contents/batch/move` would match
    `/contents/{title_contents_id}/move` and 422 on the type check. The single-edge
    routes carry an `:int` converter for that reason; relying on registration order
    alone would mean a future reorder of the module silently breaks these routes.
    """

    def test_batch_attach_is_not_matched_by_the_single_edge_route(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)

        response = client.post(
            f"/api/titles/{parent}/contents/batch",
            json={"items": _asset_items(_assets(media_repository, 1))},
        )

        assert response.status_code == HTTPStatus.CREATED, response.text

    def test_a_non_numeric_edge_id_is_now_a_404(self, client: TestClient, title_repository) -> None:
        """The converter's one behaviour change: `/contents/abc` no longer reaches the
        route to be rejected by validation, it simply matches nothing."""
        parent = _title(title_repository)

        response = client.patch(f"/api/titles/{parent}/contents/abc", json={"label": "x"})

        assert response.status_code == HTTPStatus.NOT_FOUND
