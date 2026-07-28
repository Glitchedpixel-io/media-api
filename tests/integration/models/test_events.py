# tests/integration/models/test_events.py
"""
Integration tests for SQLAlchemy event handlers.

These tests verify that the event listeners in app.models.events are triggered
correctly when model attributes change, using a real database session.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import TransformRequestORM
from app.schemas import (
    OutcomeEnum,
    TransformRequestUpdateInternal,
    TransformTypeEnum,
)
from tests.factories import AssetCreateFactory, TransformRequestCreateFactory


@pytest.mark.integration
def test_outcome_change_event_is_triggered(
    db_session: Session,
    media_repository,
    transform_request_repository,
):
    """Test that the after_update event fires when outcome changes."""
    # Create an asset and transform request
    asset = media_repository.create(AssetCreateFactory())
    tr_create = TransformRequestCreateFactory(asset_id=asset.id, outcome=None)
    tr = transform_request_repository.create(tr_create)

    # Mock the event handler to verify it's called
    with patch("app.models.events._follow_on_orchestration") as mock_trigger:
        # Update the outcome
        tr_update = TransformRequestUpdateInternal(
            outcome=OutcomeEnum.succeeded,
            actioned=True,
            processed_at=datetime.now(UTC),
        )
        transform_request_repository.update(tr.id, tr_update)

        # Verify the event handler was called
        assert mock_trigger.called
        call_args = mock_trigger.call_args

        # Check that the transform request and new outcome were passed
        assert call_args[0][0].id == tr.id  # transform_request
        assert call_args[0][1] == OutcomeEnum.succeeded  # outcome


@pytest.mark.integration
def test_on_success_actions_triggered(
    db_session: Session,
    media_repository,
    transform_request_repository,
):
    """Test that on_success actions are processed when outcome is succeeded."""
    # Create an asset and transform request with on_success config
    asset = media_repository.create(AssetCreateFactory())
    on_success_config = {"action": "create_follow_on", "transform_type": "transcode"}
    tr_create = TransformRequestCreateFactory(
        asset_id=asset.id,
        outcome=None,
        on_success=on_success_config,
    )
    tr = transform_request_repository.create(tr_create)

    # Mock the trigger function to verify on_success is accessed
    with patch("app.models.events._follow_on_orchestration") as mock_trigger:
        # Update to succeeded
        tr_update = TransformRequestUpdateInternal(
            outcome=OutcomeEnum.succeeded,
            actioned=True,
            processed_at=datetime.now(UTC),
        )
        transform_request_repository.update(tr.id, tr_update)

        # Verify it was called with the transform request that has on_success
        assert mock_trigger.called
        transform_request = mock_trigger.call_args[0][0]
        assert transform_request.on_success == on_success_config


@pytest.mark.integration
def test_on_failure_actions_triggered(
    db_session: Session,
    media_repository,
    transform_request_repository,
):
    """Test that on_failure actions are processed when outcome is failed."""
    # Create an asset and transform request with on_failure config
    asset = media_repository.create(AssetCreateFactory())
    on_failure_config = {"action": "alert", "notify": "admin@example.com"}
    tr_create = TransformRequestCreateFactory(
        asset_id=asset.id,
        outcome=None,
        on_failure=on_failure_config,
    )
    tr = transform_request_repository.create(tr_create)

    # Update to failed
    with patch("app.models.events._follow_on_orchestration") as mock_trigger:
        tr_update = TransformRequestUpdateInternal(
            outcome=OutcomeEnum.failed,
            actioned=True,
            processed_at=datetime.now(UTC),
        )
        transform_request_repository.update(tr.id, tr_update)

        # Verify it was called with the transform request that has on_failure
        assert mock_trigger.called
        transform_request = mock_trigger.call_args[0][0]
        assert transform_request.on_failure == on_failure_config


@pytest.mark.integration
def test_no_event_when_outcome_unchanged(
    db_session: Session,
    media_repository,
    transform_request_repository,
):
    """Test that the event handler doesn't trigger when outcome doesn't change."""
    # Create an asset and transform request that's already succeeded
    asset = media_repository.create(AssetCreateFactory())
    tr_create = TransformRequestCreateFactory(
        asset_id=asset.id,
        outcome=OutcomeEnum.succeeded,
        actioned=True,
        processed_at=datetime.now(UTC),
    )
    tr = transform_request_repository.create(tr_create)

    with patch("app.models.events._follow_on_orchestration") as mock_trigger:
        # Update something other than outcome
        tr_update = TransformRequestUpdateInternal(worker_notes="Updated notes")
        transform_request_repository.update(tr.id, tr_update)

        # Verify the event handler was NOT called for outcome change
        assert not mock_trigger.called


@pytest.mark.integration
def test_event_fires_for_all_outcome_types(
    db_session: Session,
    media_repository,
    transform_request_repository,
):
    """Test that the event fires for succeeded, failed, and cancelled outcomes."""
    asset = media_repository.create(AssetCreateFactory())

    for outcome in [OutcomeEnum.succeeded, OutcomeEnum.failed, OutcomeEnum.cancelled]:
        # Create a new transform request for each outcome
        tr_create = TransformRequestCreateFactory(asset_id=asset.id, outcome=None)
        tr = transform_request_repository.create(tr_create)

        with patch("app.models.events._follow_on_orchestration") as mock_trigger:
            tr_update = TransformRequestUpdateInternal(
                outcome=outcome,
                actioned=True,
                processed_at=datetime.now(UTC),
            )
            transform_request_repository.update(tr.id, tr_update)

            # Verify called with the correct outcome
            assert mock_trigger.called
            assert mock_trigger.call_args[0][1] == outcome


@pytest.mark.integration
def test_event_fires_via_orm_update(db_session: Session, media_repository):
    """Test that the event fires even with direct ORM updates (not via repository)."""
    # Create an asset and transform request directly via ORM
    asset = media_repository.create(AssetCreateFactory())
    tr_orm = TransformRequestORM(
        asset_id=asset.id,
        transform_type=TransformTypeEnum.test,
        actioned=False,
        outcome=None,
    )
    db_session.add(tr_orm)
    db_session.commit()
    db_session.refresh(tr_orm)

    with patch("app.models.events._follow_on_orchestration") as mock_trigger:
        # Update directly via ORM
        tr_orm.outcome = OutcomeEnum.succeeded
        tr_orm.actioned = True
        tr_orm.processed_at = datetime.now(UTC)
        db_session.commit()

        # Verify the event handler was called
        assert mock_trigger.called
        assert mock_trigger.call_args[0][1] == OutcomeEnum.succeeded
