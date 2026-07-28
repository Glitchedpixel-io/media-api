# app/runners/webhook_runner.py
"""A generic webhook dispatcher -- reference implementation.

This proves the design is not Prefect-shaped: any system that can receive an
HTTP ``POST`` (Celery via a bridge, Argo, n8n, a custom worker manager, ...) can
subscribe to "job ready" signals with zero adapter code. It has no log source:
workers ship their per-line logs directly to their own log store, not through
the API.
"""

from __future__ import annotations

import logfire

from app.runners.protocols import JobDispatch


class WebhookDispatcher:
    def __init__(self, url: str, timeout: float = 2.0) -> None:
        self._url = url
        self._timeout = timeout

    def dispatch(self, job: JobDispatch) -> str | None:
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
        return None
