# app/runners/protocols.py
"""Backend-agnostic seams for offloading transform work.

The durable job queue (the ``media_transform_requests`` table plus the public
HTTP API) is the source of truth. A backend only needs to satisfy two tiny,
independent seams:

* :class:`JobDispatcher` -- a best-effort "work is ready" signal.
* :class:`JobLogSource`  -- an optional way to read a job's logs.

Prefect is one implementation of these seams; so are a webhook fan-out, Celery,
Temporal, k8s Jobs, or nothing at all (the pure pull model).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class JobDispatch:
    """The payload describing a unit of work that is ready to be picked up."""

    job_id: int  # the transform_request id (the real source of truth)
    job_type: str  # e.g. "transcode", "transcribe"
    parameters: dict | None = None  # payload, for backends that carry it


@dataclass(frozen=True)
class LogEntry:
    """A single normalised log line surfaced by a :class:`JobLogSource`."""

    timestamp: str
    level: str
    logger: str | None
    message: str
    external_ref: str | None


@runtime_checkable
class JobDispatcher(Protocol):
    def dispatch(self, job: JobDispatch) -> str | None:
        """Best-effort 'work is ready' signal.

        Returns an optional backend reference (what used to be ``flow_run_id``),
        or ``None``. Must never raise into the request path.
        """
        ...


@runtime_checkable
class JobLogSource(Protocol):
    def fetch_logs(self, external_ref: str) -> list[LogEntry]:
        """Return the logs a backend holds for ``external_ref`` (may be empty)."""
        ...


@runtime_checkable
class JobRunner(Protocol):
    """Convenience facade combining the two seams.

    Backends that only implement one seam can still be exposed as a runner via
    :class:`app.runners.facade.CompositeJobRunner`, which no-ops the missing half.
    """

    def dispatch(self, job: JobDispatch) -> str | None: ...

    def fetch_logs(self, external_ref: str) -> list[LogEntry]: ...
