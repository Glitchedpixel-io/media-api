# app/runners/facade.py
from __future__ import annotations

from app.runners.protocols import JobDispatch, JobDispatcher, JobLogSource, LogEntry


class CompositeJobRunner:
    """Combine an optional dispatcher and an optional log source into a runner.

    Either half may be absent: a missing dispatcher makes ``dispatch`` a no-op
    returning ``None`` (pure pull model), and a missing log source makes
    ``fetch_logs`` return an empty list. This lets a deployment mix and match --
    e.g. dispatch via a webhook while reading logs from the DB, or dispatch via
    Prefect with no log source at all.
    """

    def __init__(
        self,
        dispatcher: JobDispatcher | None = None,
        log_source: JobLogSource | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._log_source = log_source

    def dispatch(self, job: JobDispatch) -> str | None:
        if self._dispatcher is None:
            return None
        return self._dispatcher.dispatch(job)

    def fetch_logs(self, external_ref: str) -> list[LogEntry]:
        if self._log_source is None:
            return []
        return self._log_source.fetch_logs(external_ref)
