# app/runners/prefect_runner.py
"""Prefect adapter for the job-runner seams.

Every ``prefect`` import lives inside this module and is performed lazily, so
Prefect is an optional extra (``pip install media-api[prefect]``) rather than a
mandatory dependency. Nothing here is imported unless ``runner_backend`` is set
to ``"prefect"``.
"""

from __future__ import annotations

import logfire

from app.runners.facade import CompositeJobRunner
from app.runners.protocols import JobDispatch, LogEntry

PREFECT_ROUTING_PREFIX = "prefect."


class PrefectDispatcher:
    """Trigger a Prefect deployment run to cut worker pick-up latency."""

    def dispatch(self, job: JobDispatch) -> str | None:
        if not job.job_type.startswith(PREFECT_ROUTING_PREFIX):
            return None
        deployment_name = job.job_type[len(PREFECT_ROUTING_PREFIX) :]
        with logfire.span("prefect_dispatch") as span:
            try:
                from prefect.deployments import run_deployment  # noqa: PLC0415

                run_deployment(name=deployment_name, timeout=0)
            except Exception as e:
                span.record_exception(e)
        return None


class PrefectLogSource:
    """Read a flow run's logs directly from Prefect's own API."""

    def __init__(self, limit: int = 100) -> None:
        self._limit = limit

    def fetch_logs(self, external_ref: str) -> list[LogEntry]:
        return _fetch_flow_run_logs(external_ref, limit=self._limit)


class PrefectJobRunner(CompositeJobRunner):
    """A runner that both dispatches to and reads logs from Prefect."""

    def __init__(self) -> None:
        super().__init__(
            dispatcher=PrefectDispatcher(),
            log_source=PrefectLogSource(),
        )


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
