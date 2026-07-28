# app/runners/null_runner.py
from __future__ import annotations

from app.runners.protocols import JobDispatch, LogEntry


class NullJobRunner:
    """The default runner: a pure pull model with no dispatch and no log source.

    ``dispatch`` is a no-op (workers pick jobs up on their next poll of
    ``/transform_requests/claim``) and ``fetch_logs`` returns nothing: per-line
    logs are shipped by workers directly to their own log store, not through
    the API, while the DB only holds structured ``run_summaries`` (not a log
    stream). This makes the app boot and pass its tests with no orchestration
    framework installed at all -- ``git clone && run`` works with just
    Postgres.
    """

    def dispatch(self, job: JobDispatch) -> str | None:
        return None

    def fetch_logs(self, external_ref: str) -> list[LogEntry]:
        return []
