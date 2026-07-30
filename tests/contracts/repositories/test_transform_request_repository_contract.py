# tests/contracts/repositories/test_transform_request_repository_contract.py
from datetime import UTC, datetime, timedelta
from time import sleep

import pytest

from app.repositories.errors import (
    CheckViolation,
    ForeignKeyViolation,
    NotFoundError,
    RecordCannotBeChanged,
    UniqueViolation,
)
from app.schemas import (
    OutcomeEnum,
    TransformRequestListParams,
    TransformRequestUpdateInternal,
)
from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    transform_request_bundler,
)
from tests.factories import AssetCreateFactory, TransformRequestCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, transform_request_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    tr = TransformRequestCreateFactory(asset_id=asset.id)
    out = bundle.transform_requests.create(tr)
    assert out.id is not None
    assert bundle.transform_requests.exists(out.id) is True
    fetched = bundle.transform_requests.get(out.id)
    assert fetched is not None
    assert fetched.transform_type == tr.transform_type


@pytest.mark.contract
def test_create_with_invalid_asset_id(bundle):

    with pytest.raises(ForeignKeyViolation):
        bundle.transform_requests.create(TransformRequestCreateFactory(asset_id=999999))


@pytest.mark.contract
def test_create_with_invalid_parent_id(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    with pytest.raises(ForeignKeyViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(parent_transform_request_id=999999, asset_id=asset.id)
        )


@pytest.mark.contract
def test_create_with_valid_parent_id(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    transform_request = bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id)
    )
    linked_request = bundle.transform_requests.create(
        TransformRequestCreateFactory(
            parent_transform_request_id=transform_request.id,
            asset_id=asset.id,
            transform_type="prefect.youtube",
        )
    )
    assert (
        linked_request
        and linked_request.parent_transform_request_id == transform_request.id
        and linked_request.asset_id == asset.id
        and linked_request.transform_type == "prefect.youtube"
    )


@pytest.mark.contract
def test_only_one_unactioned_per_type_per_asset(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    # Create one unactioned transcode request
    tr1 = TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode")
    assert bundle.transform_requests.create(tr1)

    # Creating another unactioned of same type for same asset should violate unique
    with pytest.raises(UniqueViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode")
        )

    # But creating a different type unactioned is allowed
    assert bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.extract_audio")
    )

    # A different provider for the same job name is a distinct routing key --
    # no collision with the prefect.transcode row above.
    assert bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="webhook.transcode")
    )


@pytest.mark.contract
def test_transform_type_filter_is_exact_match(bundle):
    """`prefect.transcode` must not match `prefect.transcode_hq` or `webhook.transcode`."""

    asset = bundle.assets.create(AssetCreateFactory())
    bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode")
    )
    bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode_hq")
    )
    bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="webhook.transcode")
    )

    response = bundle.transform_requests.list_paged(
        TransformRequestListParams(transform_type="prefect.transcode")
    )
    assert [it.transform_type for it in response.items] == ["prefect.transcode"]


@pytest.mark.contract
def test_claim_next_exact_match_skips_non_matching_types(bundle):
    """claim_next only claims exact routing-key matches, and raises when none remain."""

    asset = bundle.assets.create(AssetCreateFactory())
    bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode_hq")
    )
    bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="webhook.transcode")
    )
    target = bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode")
    )

    claimed = bundle.transform_requests.claim_next("prefect.transcode", "worker-1", None)
    assert claimed.id == target.id
    assert claimed.worker == "worker-1"

    # The one matching row is now claimed; the non-matching ones must
    # not be picked up.
    with pytest.raises(NotFoundError):
        bundle.transform_requests.claim_next("prefect.transcode", "worker-2", None)


@pytest.mark.contract
def test_free_text_provider_local_type_round_trip(bundle):
    """A provider-local type containing dots is stored and returned verbatim."""

    asset = bundle.assets.create(AssetCreateFactory())
    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(
            asset_id=asset.id, transform_type="webhook.thumbnail.generate"
        )
    )
    fetched = bundle.transform_requests.get(tr.id)
    assert fetched is not None
    assert fetched.transform_type == "webhook.thumbnail.generate"


@pytest.mark.contract
def test_many_actioned_allowed_per_type_per_asset(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    # Actioned requests should not be constrained by the unique pending index
    for i in range(5):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id,
                transform_type="prefect.transcode",
                actioned=True,
                processed_at=datetime.now(UTC),
                outcome="succeeded",
            )
        )
    assert len(bundle.transform_requests.get_asset_transform_requests(asset.id)) == 5


@pytest.mark.contract
def test_many_unactioned_allowed_per_type_for_distinct_assets(bundle):

    # Actioned requests should not be constrained by the unique pending index
    for i in range(5):
        asset = bundle.assets.create(AssetCreateFactory())
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id,
                transform_type="prefect.transcode",
                actioned=False,
                processed_at=None,
                outcome=None,
            )
        )
    response = bundle.transform_requests.list_paged(
        TransformRequestListParams(transform_type="prefect.transcode", actioned=False)
    )
    assert len(response.items) == 5


@pytest.mark.contract
def test_invalid_actioned_logic_constraints(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    # actioned=True must have processed_at and outcome
    with pytest.raises(CheckViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id, actioned=True, processed_at=None, outcome=None
            )
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id, actioned=True, processed_at=None, outcome="failed"
            )
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id,
                actioned=True,
                processed_at=datetime.now(UTC),
                outcome=None,
            )
        )


@pytest.mark.contract
def test_invalid_not_actioned_logic_constraints(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    # actioned=False must have no processed_at and no outcome
    with pytest.raises(CheckViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id,
                actioned=False,
                processed_at=datetime.now(UTC),
                outcome=None,
            )
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id,
                actioned=False,
                processed_at=datetime.now(UTC),
                outcome="failed",
            )
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.create(
            TransformRequestCreateFactory(
                asset_id=asset.id,
                actioned=False,
                processed_at=None,
                outcome="failed",
            )
        )


@pytest.mark.contract
def test_update_roundtrip_and_unset_fields(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(
            asset_id=asset.id,
            transform_type="prefect.transcode",
            actioned=False,
            worker_notes="Initial",
        )
    )

    # valid action to mark as processed
    updated = bundle.transform_requests.update(
        tr.id,
        TransformRequestUpdateInternal.model_validate(
            {
                "actioned": True,
                "processed_at": datetime.now(UTC),
                "outcome": "failed",
                "worker_notes": "Note updated",
            }
        ),
    )
    assert updated.actioned is True
    assert updated.processed_at is not None
    assert updated.outcome == OutcomeEnum.failed
    assert updated.worker_notes == "Note updated"

    # now revert to unactioned by setting all three in sync
    updated2 = bundle.transform_requests.update(
        tr.id,
        TransformRequestUpdateInternal.model_validate(
            {"actioned": False, "processed_at": None, "outcome": None}
        ),
    )
    assert updated2.actioned is False
    assert updated2.processed_at is None
    assert updated2.outcome is None


@pytest.mark.contract
def test_update_invalid_actioned_states(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, transform_type="prefect.transcode")
    )

    with pytest.raises(CheckViolation):
        bundle.transform_requests.update(
            tr.id, TransformRequestUpdateInternal.model_validate({"actioned": True})
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.update(
            tr.id,
            TransformRequestUpdateInternal.model_validate(
                {"actioned": True, "processed_at": datetime.now(UTC)}
            ),
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.update(
            tr.id,
            TransformRequestUpdateInternal.model_validate(
                {"actioned": True, "outcome": "succeeded"}
            ),
        )

    tr2 = bundle.transform_requests.create(
        TransformRequestCreateFactory(
            asset_id=asset.id,
            transform_type="prefect.transcode",
            actioned=True,
            processed_at=datetime.now(UTC),
            outcome="succeeded",
        )
    )
    assert tr2.actioned is True
    with pytest.raises(CheckViolation):
        bundle.transform_requests.update(
            tr2.id, TransformRequestUpdateInternal.model_validate({"actioned": False})
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.update(
            tr2.id,
            TransformRequestUpdateInternal.model_validate(
                {"actioned": False, "processed_at": None}
            ),
        )
    with pytest.raises(CheckViolation):
        bundle.transform_requests.update(
            tr2.id,
            TransformRequestUpdateInternal.model_validate({"actioned": False, "outcome": None}),
        )


@pytest.mark.contract
def test_update_not_found_raises(bundle):

    with pytest.raises(NotFoundError):
        bundle.transform_requests.update(
            99999,
            TransformRequestUpdateInternal.model_validate({"worker": "abc"}),
        )


@pytest.mark.contract
def test_get_asset_transform_requests(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    other = bundle.assets.create(AssetCreateFactory())
    # create several
    for t in ["prefect.transcode", "prefect.extract_audio", "prefect.youtube"]:
        bundle.transform_requests.create(
            TransformRequestCreateFactory(asset_id=asset.id, transform_type=t)
        )
    bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=other.id, transform_type="prefect.transcode")
    )
    got = bundle.transform_requests.get_asset_transform_requests(asset.id)
    assert all(gr.asset_id == asset.id for gr in got)
    assert len(got) == 3


@pytest.mark.contract
def test_list_paged_filters_and_ordering(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    # Create 6 requests spanning different attributes
    now = datetime.now(UTC)
    items = []
    for i, t in enumerate(
        [
            "prefect.transcode",
            "prefect.transcode",
            "prefect.extract_audio",
            "prefect.youtube",
            "prefect.transcode",
            "prefect.extract_audio",
        ]
    ):
        actioned = i % 2 == 0
        processed_at = now - timedelta(minutes=i) if actioned else None
        outcome = "succeeded" if actioned else None
        worker = f"w{i}" if i >= 3 else None
        items.append(
            bundle.transform_requests.create(
                TransformRequestCreateFactory(
                    asset_id=asset.id,
                    transform_type=t,
                    actioned=actioned,
                    processed_at=processed_at,
                    outcome=outcome,
                    worker=worker,
                )
            )
        )

    # Filter by type
    response = bundle.transform_requests.list_paged(
        TransformRequestListParams(transform_type="prefect.transcode")
    )
    assert all(it.transform_type == "prefect.transcode" for it in response.items)
    assert len(response.items) == len([x for x in items if x.transform_type == "prefect.transcode"])

    # Filter by actioned
    response = bundle.transform_requests.list_paged(TransformRequestListParams(actioned=True))
    assert all(it.actioned for it in response.items)

    # Filter by worker_assigned
    response = bundle.transform_requests.list_paged(
        TransformRequestListParams(worker_assigned=True)
    )
    assert all(it.worker is not None for it in response.items)
    response = bundle.transform_requests.list_paged(
        TransformRequestListParams(worker_assigned=False)
    )
    assert all(it.worker is None for it in response.items)

    # Filter by outcome
    response = bundle.transform_requests.list_paged(
        TransformRequestListParams(outcome=OutcomeEnum.succeeded)
    )
    assert all((it.outcome == OutcomeEnum.succeeded) for it in response.items)

    # Ordering: created_at desc (default sort) - first item should be the last created
    response = bundle.transform_requests.list_paged(TransformRequestListParams(limit=100))
    assert len(response.items) == len(items)
    times = [it.created_at for it in response.items]
    assert times == sorted(times, reverse=True)


@pytest.mark.contract
def test_mark_heartbeat_not_found_raises(bundle):

    with pytest.raises(NotFoundError):
        bundle.transform_requests.mark_heartbeat(999999)


@pytest.mark.contract
def test_mark_heartbeat_on_unactioned_sets_heartbeats(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, actioned=False)
    )

    # First heartbeat should be accepted and timestamps recorded
    bundle.transform_requests.mark_heartbeat(tr.id)
    got = bundle.transform_requests.get(tr.id)
    assert got.first_heartbeat is not None
    assert got.last_heartbeat is not None
    # first should be no later than last
    assert got.first_heartbeat <= got.last_heartbeat


@pytest.mark.contract
def test_mark_heartbeat_rejected_when_actioned(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    now = datetime.now(UTC)
    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(
            asset_id=asset.id,
            actioned=True,
            processed_at=now,
            outcome=OutcomeEnum.succeeded,
        )
    )

    with pytest.raises(RecordCannotBeChanged):
        bundle.transform_requests.mark_heartbeat(tr.id)


@pytest.mark.contract
def test_first_heartbeat_only_set_on_first_call(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, actioned=False)
    )

    # First call sets both first and last
    bundle.transform_requests.mark_heartbeat(tr.id)
    first = bundle.transform_requests.get(tr.id)
    assert first.first_heartbeat is not None
    assert first.last_heartbeat is not None

    # Second call should not change first_heartbeat, only last_heartbeat
    sleep(1.1)
    bundle.transform_requests.mark_heartbeat(tr.id)
    second = bundle.transform_requests.get(tr.id)
    assert second.first_heartbeat == first.first_heartbeat
    assert second.last_heartbeat is not None
    assert second.last_heartbeat >= first.last_heartbeat


@pytest.mark.contract
def test_last_heartbeat_updates_every_time(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tr = bundle.transform_requests.create(
        TransformRequestCreateFactory(asset_id=asset.id, actioned=False)
    )

    bundle.transform_requests.mark_heartbeat(tr.id)
    a = bundle.transform_requests.get(tr.id)
    assert a.last_heartbeat is not None
    prev_last = a.last_heartbeat

    sleep(1.1)
    bundle.transform_requests.mark_heartbeat(tr.id)
    b = bundle.transform_requests.get(tr.id)
    assert b.last_heartbeat is not None
    assert b.last_heartbeat >= prev_last


@pytest.mark.contract
def test_claim_next_and_not_found(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    # Create 3 unactioned, unassigned transcode and 1 assigned one
    ids = []
    for i, t in enumerate(
        [
            "prefect.transcode",
            "prefect.extract_audio",
            "prefect.youtube",
        ]
    ):
        ids.append(
            bundle.transform_requests.create(
                TransformRequestCreateFactory(asset_id=asset.id, transform_type=t)
            ).id
        )

    # Assign one to a worker already
    claimed = bundle.transform_requests.claim_next("prefect.transcode", "worker-1", None)
    assert claimed.worker == "worker-1"
    assert claimed.actioned is False
    # Next claim returns the next oldest unassigned of same type
    claimed2 = bundle.transform_requests.claim_next("prefect.extract_audio", "worker-2", None)
    assert (
        claimed2.worker == "worker-2"
        and claimed2.actioned is False
        and claimed2.id in ids
        and claimed2.transform_type == "prefect.extract_audio"
    )

    # If none available of a different type, raises NotFoundError
    with pytest.raises(NotFoundError):
        bundle.transform_requests.claim_next("prefect.test", "worker-x", None)
