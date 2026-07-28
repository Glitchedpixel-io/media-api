# app/models/events.py

import logfire

from sqlalchemy import event, inspect

from app.models.transform_request import TransformRequestORM
from app.schemas.enums import OutcomeEnum


@event.listens_for(TransformRequestORM, "after_update")
def handle_outcome_change(mapper, connection, target: TransformRequestORM) -> None:  # type: ignore
    insp = inspect(target)
    history = insp.attrs.outcome.history

    # history.added contains the new value(s)
    # history.deleted contains the old value(s)
    if history.has_changes():
        old_outcome = history.deleted[0] if history.deleted else None
        new_outcome = history.added[0] if history.added else None

        # Only process if outcome actually changed
        if old_outcome != new_outcome and new_outcome is not None:
            logfire.info(
                f"Transform request {target.id} outcome changed: {old_outcome} -> {new_outcome}"
            )

            # hand off to the follow-on action handler
            # _trigger_follow_on_actions(target, new_outcome, connection)
            _follow_on_orchestration(target, new_outcome)


def _follow_on_orchestration(
    transform_request: TransformRequestORM,
    outcome: OutcomeEnum,
) -> None:
    actions = _get_follow_on_actions(transform_request, outcome)
    if actions:
        logfire.debug(
            f"Follow-on actions for transform request {transform_request.id} ({outcome.value}): {actions}"
        )
        # These imports must remain here: app.models is imported by app.db (via init_db),
        # and app.handlers/app.schemas transitively import app.models — a genuine cycle.
        from app.config import get_runner_config  # noqa: PLC0415
        from app.database import get_session_factory  # noqa: PLC0415
        from app.handlers import TransformActionsHandler  # noqa: PLC0415
        from app.runners import build_job_runner  # noqa: PLC0415
        from app.schemas import TransformRequestReadExpanded  # noqa: PLC0415

        session = get_session_factory()()
        try:
            handler = TransformActionsHandler(session, build_job_runner(get_runner_config()))

            # Process actions
            handler.process_actions(
                TransformRequestReadExpanded.model_validate(transform_request), actions
            )
        except Exception as e:
            session.rollback()
            logfire.exception(
                f"Failed to process on_success actions for transform {transform_request.id}: {e}",
            )
        finally:
            session.close()

    else:
        logfire.debug(
            f"No follow-on actions found for transform request {transform_request.id} ({outcome.value})"
        )


def _get_follow_on_actions(tr: TransformRequestORM, outcome: OutcomeEnum) -> dict | None:
    if outcome == OutcomeEnum.succeeded and tr.on_success:
        return tr.on_success
    elif outcome == OutcomeEnum.failed and tr.on_failure:
        return tr.on_failure
    else:
        return None
