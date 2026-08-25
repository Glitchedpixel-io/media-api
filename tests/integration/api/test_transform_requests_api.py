# tests/integration/api/test_transform_requests_api.py
from __future__ import annotations

import concurrent.futures
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from app.repositories.protocols import MediaRepository, TransformRequestRepository
from app.schemas import AssetCreateInternal, TransformRequestCreateInternal
from tests.factories import AssetReadFactory, TransformRequestReadFactory

TRANSFORM_TYPE = "prefect.test"


def _create_asset(media_repository: MediaRepository) -> int:
    asset = AssetReadFactory()
    created = media_repository.create(
        AssetCreateInternal(
            **asset.model_dump(exclude={"id", "created_at", "master_asset_id"})  # type: ignore
        )
    )
    return created.id


def _seed_claimable(
    media_repository: MediaRepository,
    transform_request_repository: TransformRequestRepository,
    count: int,
) -> None:
    """Create `count` transform requests that are available to claim.

    Each gets its own asset: ``uniq_pending_transform_per_asset_and_type`` allows
    only one pending transform per asset per type, so a queue of N claimable
    requests of one type is necessarily spread over N assets.
    """
    for _ in range(count):
        asset_id = _create_asset(media_repository)
        request = TransformRequestReadFactory(asset_id=asset_id, transform_type=TRANSFORM_TYPE)
        transform_request_repository.create(
            TransformRequestCreateInternal(
                **request.model_dump(  # type: ignore
                    exclude={"id", "created_at", "first_heartbeat", "last_heartbeat"}
                )
            )
        )


@pytest.mark.integration
@pytest.mark.api
class TestClaimConcurrency:
    """Concurrency guarantees of POST /api/transform_requests/claim.

    ``claim_next`` selects with ``FOR UPDATE SKIP LOCKED`` and holds the row lock
    until it commits, so concurrent claimers must never be handed the same
    request. Nothing else asserts that: the repository-level claim tests share a
    single session, which cannot produce two competing transactions.

    These tests need no sleeps or barriers -- SKIP LOCKED makes the outcome
    deterministic, each transaction taking a distinct row or none. If one ever
    needs a delay to pass, the lock is not spanning select-to-commit and that is
    the bug, not a flaky test.

    Both were checked against a mutant with ``.with_for_update(skip_locked=True)``
    removed: the first then reports one request handed to three workers, the
    second four claims from a queue of two.
    """

    def _claim(self, client: TestClient, worker: str):  # type: ignore[no-untyped-def]
        return client.post(
            "/api/transform_requests/claim",
            json={
                "transform_type": TRANSFORM_TYPE,
                "worker": worker,
                "external_job_id": None,
            },
        )

    def _claim_concurrently(self, client: TestClient, workers: int) -> list:  # type: ignore[type-arg]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self._claim, client, f"worker-{i}") for i in range(workers)]
            return [f.result() for f in futures]

    def test_concurrent_claims_never_hand_out_the_same_request(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        """Six workers, six requests: every worker gets a different one."""
        _seed_claimable(media_repository, transform_request_repository, count=6)

        responses = self._claim_concurrently(client, workers=6)

        assert all(r.status_code == HTTPStatus.OK for r in responses)
        claimed_ids = [r.json()["id"] for r in responses]
        assert len(set(claimed_ids)) == len(
            claimed_ids
        ), f"same request claimed twice: {claimed_ids}"

    def test_concurrent_claims_do_not_oversubscribe_a_short_queue(
        self,
        client: TestClient,
        media_repository: MediaRepository,
        transform_request_repository: TransformRequestRepository,
    ) -> None:
        """Six workers, two requests: exactly two win, the rest get 204."""
        _seed_claimable(media_repository, transform_request_repository, count=2)

        responses = self._claim_concurrently(client, workers=6)

        claimed = [r for r in responses if r.status_code == HTTPStatus.OK]
        empty = [r for r in responses if r.status_code == HTTPStatus.NO_CONTENT]

        assert len(claimed) == 2, f"expected 2 claims, got {[r.status_code for r in responses]}"
        assert len(empty) == 4
        assert len({r.json()["id"] for r in claimed}) == 2
