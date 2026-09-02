# tests/integration/api/test_containment_concurrency.py

"""Concurrent containment writes to one parent must not lose each other (#193).

Every write in `title_content_repository` that touches a list computes an index from
that list -- `create_positioned` appends at `len(rows)`, `reorder` renumbers around a
chosen index. `_locked_lists` was meant to serialise them, and on its own it cannot:
`SELECT ... FOR UPDATE` locks **the rows it returned**, and the row a concurrent
transaction is about to insert is a phantom, outside that set. Two appends therefore
computed the same position and one lost, at commit, to the deferred
`uq_parent_position` -- a flat 409 and a write silently discarded.

Measured before the fix, 32 attaches to one parent across 8 workers: **12 landed, 20
were rejected**, and the parallel run was *slower* than doing it sequentially. A parent
that already held 20 rows raced just as badly, which is what ruled out "an empty list has
no rows to lock" as the whole story -- the contended value is a row that does not exist
yet, so how many exist is irrelevant.

The fix locks the **parent title row**, which exists whether or not the list does. These
tests are what say it holds. They are deliberately threaded rather than mocked: the
integration harness gives one session per request (see `tests/integration/conftest.py`),
so this is real contention between real transactions, and nothing short of that would
have found the defect.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import MediaRepository, TitleRepository
from app.schemas import AssetCreateInternal
from tests.factories import AssetReadFactory, TitleReadFactory, get_title_internal

#: Enough concurrency to lose writes reliably before the fix, small enough to stay quick.
WORKERS = 8
ITEMS = 16


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


def _in_parallel(work, items):
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(work, items))


def _assert_well_formed(client: TestClient, parent: int, expected: int) -> list[dict]:
    """Every row landed, and the list is still contiguous and zero-based."""
    rows = _contents(client, parent)
    assert len(rows) == expected
    assert [row["position"] for row in rows] == list(range(expected))
    return rows


@pytest.mark.api
@pytest.mark.integration
class TestConcurrentAttach:
    """`POST /api/titles/{id}/contents` from several callers at once."""

    def test_every_concurrent_attach_lands(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """The regression. Before the fix roughly two thirds of these were rejected."""
        parent = _title(title_repository)
        asset_ids = _assets(media_repository, ITEMS)

        def attach(asset_id: int) -> tuple[int, str]:
            response = client.post(
                f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset_id}
            )
            return response.status_code, response.text

        results = _in_parallel(attach, asset_ids)

        statuses = Counter(status for status, _ in results)
        rejected = [body for status, body in results if status != HTTPStatus.CREATED]
        assert statuses == {HTTPStatus.CREATED: ITEMS}, f"rejected: {rejected[:3]}"
        _assert_well_formed(client, parent, ITEMS)

    def test_concurrent_attach_to_an_already_populated_parent(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """Rows existing to lock is not what made it safe, so it is worth both cases."""
        parent = _title(title_repository)
        for asset_id in _assets(media_repository, 5):
            response = client.post(
                f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset_id}
            )
            assert response.status_code == HTTPStatus.CREATED, response.text

        def attach(asset_id: int) -> int:
            return client.post(
                f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset_id}
            ).status_code

        statuses = Counter(_in_parallel(attach, _assets(media_repository, ITEMS)))

        assert statuses == {HTTPStatus.CREATED: ITEMS}
        _assert_well_formed(client, parent, 5 + ITEMS)

    def test_concurrent_attach_to_different_parents_is_unaffected(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """The lock is per parent, so unrelated lists must not queue behind each other.

        This asserts correctness rather than timing -- a wall-clock assertion would be
        the kind of test that fails on a loaded CI runner for no reason.
        """
        parents = [_title(title_repository) for _ in range(4)]
        asset_ids = _assets(media_repository, len(parents))
        pairs = list(zip(parents, asset_ids))

        def attach(pair: tuple[int, int]) -> int:
            parent, asset_id = pair
            return client.post(
                f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset_id}
            ).status_code

        assert Counter(_in_parallel(attach, pairs)) == {HTTPStatus.CREATED: len(pairs)}
        for parent in parents:
            _assert_well_formed(client, parent, 1)


@pytest.mark.api
@pytest.mark.integration
class TestConcurrentReorderAndMove:
    """The other two writes that compute an index from a list they have locked."""

    def test_concurrent_reorders_within_one_parent_all_land(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)
        edges = []
        for asset_id in _assets(media_repository, ITEMS):
            response = client.post(
                f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset_id}
            )
            assert response.status_code == HTTPStatus.CREATED, response.text
            edges.append(response.json()["id"])

        def send_to_front(edge: int) -> int:
            return client.patch(
                f"/api/titles/{parent}/contents/{edge}/reorder", params={"position": "start"}
            ).status_code

        statuses = Counter(_in_parallel(send_to_front, edges))

        assert statuses == {HTTPStatus.OK: ITEMS}
        _assert_well_formed(client, parent, ITEMS)

    def test_concurrent_moves_into_one_destination_all_land(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """A bulk drag into one collection, which is the gesture #179 is about."""
        destination = _title(title_repository)
        edges = []
        for asset_id in _assets(media_repository, ITEMS):
            source = _title(title_repository)
            response = client.post(
                f"/api/titles/{source}/contents", json={"kind": "asset", "asset_id": asset_id}
            )
            assert response.status_code == HTTPStatus.CREATED, response.text
            edges.append(response.json()["id"])

        def move(edge: int) -> int:
            return client.post(
                f"/api/titles/{destination}/contents/{edge}/move", params={"position": "end"}
            ).status_code

        statuses = Counter(_in_parallel(move, edges))

        assert statuses == {HTTPStatus.OK: ITEMS}
        _assert_well_formed(client, destination, ITEMS)

    def test_opposing_cross_parent_moves_do_not_deadlock(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        """Two parents, edges moving both ways at once.

        A cross-parent write locks two rows, and locking them in an order that depends on
        which way the edge is travelling is how a pair of opposing moves deadlocks. The
        parents are locked in id order for that reason; this is what would catch losing
        it.
        """
        left = _title(title_repository)
        right = _title(title_repository)
        moving: list[tuple[int, int]] = []
        for index, asset_id in enumerate(_assets(media_repository, ITEMS)):
            home, destination = (left, right) if index % 2 == 0 else (right, left)
            response = client.post(
                f"/api/titles/{home}/contents", json={"kind": "asset", "asset_id": asset_id}
            )
            assert response.status_code == HTTPStatus.CREATED, response.text
            moving.append((response.json()["id"], destination))

        def move(pair: tuple[int, int]) -> int:
            edge, destination = pair
            return client.post(
                f"/api/titles/{destination}/contents/{edge}/move", params={"position": "end"}
            ).status_code

        statuses = Counter(_in_parallel(move, moving))

        assert statuses == {HTTPStatus.OK: ITEMS}
        assert len(_contents(client, left)) + len(_contents(client, right)) == ITEMS
        for parent in (left, right):
            rows = _contents(client, parent)
            assert [row["position"] for row in rows] == list(range(len(rows)))


@pytest.mark.api
@pytest.mark.integration
class TestConcurrentDeleteAgainstReorder:
    """Delete used to take its locks the other way round.

    Every other write locks the parent title first and the contents rows second.
    `delete_title_content` deleted the row -- taking a lock on it -- and locked the
    parent afterwards, which is the same A-holds-what-B-wants shape that made opposing
    moves deadlock. Nothing in the API paired a delete with a reorder, so it never
    surfaced; the order is normalised rather than left resting on that.
    """

    def test_deletes_and_reorders_on_one_parent_interleave_cleanly(
        self, client: TestClient, title_repository, media_repository
    ) -> None:
        parent = _title(title_repository)
        edges = []
        for asset_id in _assets(media_repository, ITEMS):
            response = client.post(
                f"/api/titles/{parent}/contents", json={"kind": "asset", "asset_id": asset_id}
            )
            assert response.status_code == HTTPStatus.CREATED, response.text
            edges.append(response.json()["id"])

        doomed, surviving = edges[: ITEMS // 2], edges[ITEMS // 2 :]

        def work(item: tuple[str, int]) -> int:
            kind, edge = item
            if kind == "delete":
                return client.delete(f"/api/titles/{parent}/contents/{edge}").status_code
            return client.patch(
                f"/api/titles/{parent}/contents/{edge}/reorder", params={"position": "start"}
            ).status_code

        jobs = [("delete", edge) for edge in doomed] + [("reorder", edge) for edge in surviving]
        statuses = Counter(_in_parallel(work, jobs))

        assert set(statuses) <= {HTTPStatus.NO_CONTENT, HTTPStatus.OK}, statuses
        rows = _assert_well_formed(client, parent, len(surviving))
        assert {row["id"] for row in rows} == set(surviving)
