# app/orchestration/prefect.py
"""The built-in Prefect orchestration provider.

Requires the optional ``prefect`` extra (``media-api[prefect]``). The module
itself -- and therefore its entry point -- is always importable without that
extra installed; only *constructing* the provider (which only happens if
``prefect`` is actually listed in ``enabled_orchestration_providers``) requires
the package to be present. Every ``prefect`` API call beyond that check stays
lazily imported, so nothing here is touched unless the provider is both
enabled and in active use.
"""

from __future__ import annotations

import importlib.util

import logfire

from app.orchestration.providers import PROVIDER_API_VERSION, TransformRoute
from app.runners.protocols import JobDispatch, LogEntry


class PrefectProvider:
    key = "prefect"
    api_version = PROVIDER_API_VERSION

    def __init__(self, log_limit: int = 100) -> None:
        if importlib.util.find_spec("prefect") is None:
            raise RuntimeError(
                "Orchestration provider 'prefect' is enabled but the 'prefect' "
                "package is not installed. Install it with: uv add 'media-api[prefect]'"
            )
        self._log_limit = log_limit

    def dispatch(self, route: TransformRoute, job: JobDispatch) -> None:
        with logfire.span("prefect_dispatch") as span:
            try:
                from prefect.deployments import run_deployment  # noqa: PLC0415

                run_deployment(name=route.command, timeout=0)
            except Exception as e:
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
