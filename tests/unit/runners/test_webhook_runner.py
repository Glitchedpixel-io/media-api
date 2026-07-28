"""Unit tests for the WebhookDispatcher reference backend."""

from __future__ import annotations

import pytest

from app.runners import JobDispatch
from app.runners.webhook_runner import WebhookDispatcher


@pytest.fixture
def job() -> JobDispatch:
    return JobDispatch(job_id=7, job_type="transcode", parameters={"quality": "hd"})


class TestWebhookDispatcher:
    @pytest.mark.unit
    def test_dispatch_posts_expected_payload(self, mocker, job) -> None:
        post = mocker.patch("requests.post")

        dispatcher = WebhookDispatcher("https://example.com/hook", timeout=1.5)
        result = dispatcher.dispatch(job)

        # The webhook backend has no log source / backend ref, so it returns None.
        assert result is None
        post.assert_called_once_with(
            "https://example.com/hook",
            json={
                "job_id": 7,
                "job_type": "transcode",
                "parameters": {"quality": "hd"},
            },
            timeout=1.5,
        )
        post.return_value.raise_for_status.assert_called_once_with()

    @pytest.mark.unit
    def test_dispatch_default_timeout(self, mocker, job) -> None:
        post = mocker.patch("requests.post")

        WebhookDispatcher("https://example.com/hook").dispatch(job)

        assert post.call_args.kwargs["timeout"] == 2.0

    @pytest.mark.unit
    def test_dispatch_swallows_request_errors(self, mocker, job) -> None:
        mocker.patch("requests.post", side_effect=RuntimeError("connection refused"))

        dispatcher = WebhookDispatcher("https://example.com/hook")

        # Errors must never escape into the request path.
        assert dispatcher.dispatch(job) is None

    @pytest.mark.unit
    def test_dispatch_swallows_http_status_errors(self, mocker, job) -> None:
        response = mocker.Mock()
        response.raise_for_status.side_effect = RuntimeError("500 Server Error")
        mocker.patch("requests.post", return_value=response)

        dispatcher = WebhookDispatcher("https://example.com/hook")

        assert dispatcher.dispatch(job) is None
