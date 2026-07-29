"""Unit tests for TransformActionsHandler."""

from __future__ import annotations

from unittest.mock import MagicMock, create_autospec

import pytest

from app.handlers.transform_actions import TransformActionsHandler
from app.runners import NullJobRunner
from app.services import TransformRequestService
from tests.factories import TransformRequestReadExpandedFactory, TransformRequestReadFactory


@pytest.fixture
def handler() -> TransformActionsHandler:
    """A handler wired to a mock session and the default (no-op) runner."""
    return TransformActionsHandler(MagicMock(), NullJobRunner())


class TestTransformActionsHandler:
    @pytest.mark.unit
    def test_init_builds_service_with_runner(self) -> None:
        handler = TransformActionsHandler(MagicMock(), NullJobRunner())

        assert isinstance(handler.service, TransformRequestService)

    @pytest.mark.unit
    def test_process_actions_without_create_is_noop(self, handler) -> None:
        handler.service = create_autospec(TransformRequestService, instance=True, spec_set=True)

        cause = TransformRequestReadExpandedFactory(id=1)
        handler.process_actions(cause, {})

        handler.service.create_linked_request.assert_not_called()

    @pytest.mark.unit
    def test_process_actions_creates_linked_requests(self, handler) -> None:
        service = create_autospec(TransformRequestService, instance=True, spec_set=True)
        service.create_linked_request.return_value = TransformRequestReadFactory(id=99)
        handler.service = service

        cause = TransformRequestReadExpandedFactory(id=1)
        actions = {
            "create": [
                {"transform_type": "prefect.transcode", "parameters": {"x": 1}},
                {"transform_type": "prefect.test"},
            ]
        }

        handler.process_actions(cause, actions)

        assert service.create_linked_request.call_count == 2

    @pytest.mark.unit
    def test_process_actions_swallows_errors(self, handler) -> None:
        service = create_autospec(TransformRequestService, instance=True, spec_set=True)
        service.create_linked_request.side_effect = RuntimeError("boom")
        handler.service = service

        cause = TransformRequestReadExpandedFactory(id=1)

        # A failing linked-request creation must not propagate.
        handler.process_actions(cause, {"create": [{"transform_type": "prefect.test"}]})

        service.create_linked_request.assert_called_once()

    @pytest.mark.unit
    def test_process_actions_invalid_transform_type_is_skipped(self, handler) -> None:
        """A shape-invalid transform_type raises inside TransformRequestCreatePublic
        construction; the handler's own try/except must record it on the span and
        skip that one follow-on without creating a linked request or raising."""
        service = create_autospec(TransformRequestService, instance=True, spec_set=True)
        handler.service = service

        cause = TransformRequestReadExpandedFactory(id=1)
        handler.process_actions(cause, {"create": [{"transform_type": "no-dot-here"}]})

        service.create_linked_request.assert_not_called()

    @pytest.mark.unit
    def test_process_actions_omitted_transform_type_is_skipped(self, handler) -> None:
        """No implicit default (e.g. the old "test") -- an omitted transform_type
        must not create a linked request, and must not raise."""
        service = create_autospec(TransformRequestService, instance=True, spec_set=True)
        handler.service = service

        cause = TransformRequestReadExpandedFactory(id=1)
        handler.process_actions(cause, {"create": [{"parameters": {"x": 1}}]})

        service.create_linked_request.assert_not_called()
