"""Unit tests for the built-in webhook orchestration provider."""

from __future__ import annotations

import pytest

from app.orchestration.providers import TransformRoute
from app.orchestration.webhook import WebhookProvider
from app.runners.protocols import JobDispatch


class TestWebhookProviderDispatch:
    @pytest.mark.unit
    def test_dispatch_posts_job_payload(self, mocker) -> None:
        post = mocker.patch("requests.post")
        post.return_value.raise_for_status.return_value = None
        provider = WebhookProvider(url="https://example.com/hook")

        provider.dispatch(
            TransformRoute(provider="webhook", command="thumbnail.generate"),
            JobDispatch(job_id=1, job_type="webhook.thumbnail.generate", parameters={"a": 1}),
        )

        post.assert_called_once_with(
            "https://example.com/hook",
            json={"job_id": 1, "job_type": "webhook.thumbnail.generate", "parameters": {"a": 1}},
            timeout=2.0,
        )

    @pytest.mark.unit
    def test_dispatch_uses_configured_timeout(self, mocker) -> None:
        post = mocker.patch("requests.post")
        post.return_value.raise_for_status.return_value = None
        provider = WebhookProvider(url="https://example.com/hook", timeout=5.0)

        provider.dispatch(
            TransformRoute(provider="webhook", command="x"),
            JobDispatch(job_id=1, job_type="webhook.x"),
        )

        assert post.call_args.kwargs["timeout"] == 5.0

    @pytest.mark.unit
    def test_dispatch_swallows_request_errors(self, mocker) -> None:
        mocker.patch("requests.post", side_effect=RuntimeError("connection refused"))
        provider = WebhookProvider(url="https://example.com/hook")

        # Must not raise.
        provider.dispatch(
            TransformRoute(provider="webhook", command="x"),
            JobDispatch(job_id=1, job_type="webhook.x"),
        )

    @pytest.mark.unit
    def test_dispatch_swallows_non_2xx_responses(self, mocker) -> None:
        post = mocker.patch("requests.post")
        post.return_value.raise_for_status.side_effect = RuntimeError("500")
        provider = WebhookProvider(url="https://example.com/hook")

        # Must not raise.
        provider.dispatch(
            TransformRoute(provider="webhook", command="x"),
            JobDispatch(job_id=1, job_type="webhook.x"),
        )


class TestWebhookProviderFetchLogs:
    @pytest.mark.unit
    def test_fetch_logs_returns_empty(self) -> None:
        provider = WebhookProvider(url="https://example.com/hook")

        assert provider.fetch_logs(TransformRoute(provider="webhook", command="x"), "ref-1") == []
