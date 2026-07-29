"""Payload types shared by the orchestration provider seams.

The pluggable backends themselves -- Prefect, a webhook fan-out, or nothing
at all -- live in :mod:`app.orchestration`, discovered via the
``media_api.orchestration_providers`` entry-point group and selected by
``enabled_orchestration_providers`` config. This package now holds only the
:class:`JobDispatch` / :class:`LogEntry` payload types that providers pass
around.
"""

from app.runners.protocols import JobDispatch, LogEntry

__all__ = [
    "JobDispatch",
    "LogEntry",
]
