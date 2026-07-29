# app/orchestration/webhook.py
"""The built-in webhook orchestration provider -- a reference implementation.

Proves the design is not Prefect-shaped: any system that can receive an HTTP
POST (Celery via a bridge, Argo, n8n, a custom worker manager, ...) can
subscribe to "job ready" signals with zero adapter code, by enabling this
provider and routing requests through ``webhook.<anything>``. No extra
install is required -- ``requests`` is already a core dependency. It has no
log source: workers ship their per-line logs directly to their own log
store, not through the API.
"""

from __future__ import annotations

import logfire

from app.orchestration.providers import PROVIDER_API_VERSION, TransformRoute
from app.runners.protocols import JobDispatch, LogEntry


class WebhookProvider:
    key = "webhook"
    api_version = PROVIDER_API_VERSION

    def __init__(self, url: str, timeout: float = 2.0) -> None:
        self._url = url
        self._timeout = timeout

    def dispatch(self, route: TransformRoute, job: JobDispatch) -> None:
        with logfire.span("webhook_dispatch") as span:
            try:
                import requests  # noqa: PLC0415

                response = requests.post(
                    self._url,
                    json={
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "parameters": job.parameters,
                    },
                    timeout=self._timeout,
                )
                response.raise_for_status()
            except Exception as e:
                span.record_exception(e)

    def fetch_logs(self, route: TransformRoute, external_job_id: str) -> list[LogEntry]:
        return []
