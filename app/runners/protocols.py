# app/runners/protocols.py
"""Payload types shared by the orchestration provider seams.

The durable job queue (the ``media_transform_requests`` table plus the public
HTTP API) is the source of truth. Orchestration providers (see
``app.orchestration``) are best-effort "work is ready" signals plus an
optional way to read a job's logs, built on these two payload types.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobDispatch:
    """The payload describing a unit of work that is ready to be picked up."""

    job_id: int  # the transform_request id (the real source of truth)
    job_type: str  # e.g. "prefect.transcode"
    parameters: dict | None = None  # payload, for backends that carry it


@dataclass(frozen=True)
class LogEntry:
    """A single normalised log line surfaced by a provider's log source."""

    timestamp: str
    level: str
    logger: str | None
    message: str
    external_ref: str | None
