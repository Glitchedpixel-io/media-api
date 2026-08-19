# app/orchestration/prefect.py
"""The built-in Prefect orchestration provider.

Requires the optional ``prefect`` extra (``media-api[prefect]``). The module
itself -- and therefore its entry point -- is always importable without that
extra installed; only *constructing* the provider (which only happens if
``prefect`` is actually listed in ``enabled_orchestration_providers``) requires
the package to be present. Every ``prefect`` API call beyond that check stays
lazily imported, so nothing here is touched unless the provider is both
enabled and in active use.

## Deployment name resolution

Prefect identifies a deployment as ``<flow name>/<deployment name>``, and
``run_deployment`` splits on the ``/`` to resolve it. A routing key's
provider-local half cannot carry that identifier directly: routing keys forbid
whitespace (``app.schemas.transform_routing``) and real deployment names
routinely contain spaces (``Probe Metadata``, ``Extract Audio``). So the
provider takes an optional ``deployments`` map from provider-local command to
full deployment identifier, supplied as a provider option:

    ORCHESTRATION_PROVIDER_OPTIONS='{"prefect": {"deployments": {
        "transcode": "transcode-flow/Transcoder",
        "extract_audio": "extract-audio-flow/Extract Audio"
    }}}'

The map is provider-scoped on purpose: the core stays allow-list-free and never
interprets a provider-local command, while the adapter that owns Prefect's
vocabulary is the one that knows how to resolve it. An unmapped command is
passed through verbatim, so the map is optional and adding a deployment whose
identifier needs no translation requires no config change.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping

import logfire

from app.orchestration.providers import PROVIDER_API_VERSION, TransformRoute
from app.runners.protocols import JobDispatch, LogEntry


class PrefectProvider:
    key = "prefect"
    api_version = PROVIDER_API_VERSION

    def __init__(self, log_limit: int = 100, deployments: Mapping[str, str] | None = None) -> None:
        if importlib.util.find_spec("prefect") is None:
            raise RuntimeError(
                "Orchestration provider 'prefect' is enabled but the 'prefect' "
                "package is not installed. Install it with: uv add 'media-api[prefect]'"
            )
        self._log_limit = log_limit
        self._deployments = dict(deployments or {})

    def dispatch(self, route: TransformRoute, job: JobDispatch) -> None:
        with logfire.span("prefect_dispatch") as span:
            deployment = self._deployments.get(route.command, route.command)
            span.set_attribute("prefect.deployment", deployment)

            if "/" not in deployment:
                # Prefect cannot resolve this: run_deployment splits the name on
                # "/" and needs both halves. Say so plainly -- an unmapped
                # command failing silently is exactly how this went unnoticed
                # before (media-runners#39).
                logfire.warn(
                    "Transform type {job_type!r} resolves to {deployment!r}, which is "
                    "not a '<flow>/<deployment>' identifier. Add it to the Prefect "
                    "provider's 'deployments' option.",
                    job_type=job.job_type,
                    deployment=deployment,
                )

            try:
                from prefect.deployments import run_deployment  # noqa: PLC0415

                run_deployment(name=deployment, timeout=0)
            except Exception as e:
                # Deliberately non-fatal: a dispatch failure must never fail the
                # request that triggered it. Logged as an error as well as
                # recorded on the span so it surfaces without reading traces.
                logfire.error(
                    "Prefect dispatch failed for job {job_id} ({job_type!r} -> "
                    "{deployment!r}): {error}",
                    job_id=job.job_id,
                    job_type=job.job_type,
                    deployment=deployment,
                    error=e,
                )
                span.record_exception(e)

    def fetch_logs(self, route: TransformRoute, external_job_id: str) -> list[LogEntry]:
        return _fetch_flow_run_logs(external_job_id, limit=self._log_limit)


def _fetch_flow_run_logs(flow_run_id: str, limit: int = 100) -> list[LogEntry]:
    """
    Synchronously fetch logs for a given Prefect flow run without nesting event loops.
    """
    import asyncio  # noqa: PLC0415

    from prefect import get_client  # noqa: PLC0415
    from prefect.client.schemas.filters import (  # noqa: PLC0415
        LogFilter,
        LogFilterFlowRunId,
    )
    from prefect.client.schemas.sorting import LogSort  # noqa: PLC0415

    async def _inner() -> list[LogEntry]:
        results: list[LogEntry] = []
        async with get_client() as client:
            offset = 0
            while True:
                batch = await client.read_logs(
                    log_filter=LogFilter(flow_run_id=LogFilterFlowRunId(any_=[flow_run_id])),
                    limit=limit,
                    offset=offset,
                    sort=LogSort.TIMESTAMP_ASC,
                )
                if not batch:
                    break
                results.extend(
                    [
                        LogEntry(
                            timestamp=str(entry.timestamp),
                            level=getattr(entry.level, "name", str(entry.level)),
                            logger=entry.name,
                            message=entry.message,
                            external_ref=(str(entry.flow_run_id) if entry.flow_run_id else None),
                        )
                        for entry in batch
                    ]
                )
                offset += len(batch)
        return results

    # Safe sync bridge: avoid asyncio.run inside an active event loop
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # Run the coroutine in a worker thread to avoid interfering with the loop
            return loop.run_until_complete(asyncio.to_thread(lambda: asyncio.run(_inner())))
    except RuntimeError:
        # No running loop
        pass

    return asyncio.run(_inner())
