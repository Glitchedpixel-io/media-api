# app/handlers/transform_actions.py
from __future__ import annotations

import logfire

from sqlalchemy.orm import Session

from app.orchestration.registry import ProviderRegistry
from app.repositories import SQLAlchemyMediaRepository, SQLAlchemyTransformRequestRepository
from app.schemas import (
    TransformRequestCreatePublic,
    TransformRequestReadExpanded,
)
from app.services import TransformRequestService


class TransformActionsHandler:
    def __init__(self, session: Session, provider_registry: ProviderRegistry) -> None:
        self.session = session
        self.service = TransformRequestService(
            SQLAlchemyTransformRequestRepository(session),
            SQLAlchemyMediaRepository(session),
            provider_registry,
        )

    def process_actions(self, cause: TransformRequestReadExpanded, actions: dict) -> None:
        """
        Processes the actions specified in the `actions` dictionary in relation to the
        `cause`. This method handles creation of linked requests based on the data
        provided in the `actions` dictionary under the "create" key.

        :param cause: The original request object used as a reference for creating
            linked requests.
        :type cause: TransformRequestReadExpanded
        :param actions: Dictionary containing action details to process. It must have
            a "create" key for creating linked requests.
        :type actions: dict
        :return: None
        """
        with logfire.span("process_transform_actions") as span:
            if "create" in actions:
                requests_to_create = actions["create"]
                for request in requests_to_create:
                    try:
                        creation = TransformRequestCreatePublic(
                            transform_type=request.get("transform_type"),
                            parameters=request.get("parameters", {}),
                            on_success=request.get("on_success", None),
                            on_failure=request.get("on_failure", None),
                        )
                        linked_request = self.service.create_linked_request(cause.id, creation)
                        logfire.debug(
                            f"Created linked request {linked_request.id} from {cause.id} of type {linked_request.transform_type}"
                        )
                    except Exception as e:
                        # Deliberately non-fatal: one unusable follow-on must not
                        # fail the transform that triggered it, nor stop the other
                        # follow-ons in this batch. Logged as an error as well as
                        # recorded on the span so it surfaces without reading
                        # traces — a follow-on that never materialises is otherwise
                        # indistinguishable from one that was never configured,
                        # which is exactly how every YouTube-import chain stayed
                        # broken unnoticed after the routing-key change (#9).
                        logfire.error(
                            "Follow-on transform {transform_type!r} from request "
                            "{cause_id} could not be created: {error}",
                            cause_id=cause.id,
                            transform_type=request.get("transform_type"),
                            error=e,
                        )
                        span.record_exception(e)
