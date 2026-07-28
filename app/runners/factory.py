# app/runners/factory.py
from __future__ import annotations

from app.config.schema import RunnerConfig
from app.runners.null_runner import NullJobRunner
from app.runners.protocols import JobRunner


def build_job_runner(config: RunnerConfig) -> JobRunner:
    """Construct the configured job runner.

    Backend adapters are imported lazily so that optional dependencies (e.g.
    Prefect) are only required when their backend is actually selected.
    """
    backend = (config.backend or "none").lower()

    if backend == "none":
        return NullJobRunner()

    if backend == "prefect":
        from app.runners.prefect_runner import PrefectJobRunner  # noqa: PLC0415

        return PrefectJobRunner(config.job_routing_map)

    if backend == "webhook":
        from app.runners.facade import CompositeJobRunner  # noqa: PLC0415
        from app.runners.webhook_runner import WebhookDispatcher  # noqa: PLC0415

        if not config.webhook_url:
            raise ValueError("runner_backend='webhook' requires runner_webhook_url to be set")
        return CompositeJobRunner(dispatcher=WebhookDispatcher(config.webhook_url))

    raise ValueError(f"Unknown runner_backend: {config.backend!r}")
