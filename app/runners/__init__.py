# app/runners/__init__.py
"""Pluggable job-execution backends.

The API depends only on the :class:`JobDispatcher` / :class:`JobLogSource`
seams (and the :class:`JobRunner` facade that combines them). Concrete backends
-- Prefect, a webhook fan-out, or nothing at all -- live behind
:func:`build_job_runner` and are selected by ``runner_backend`` config.
"""

from app.runners.facade import CompositeJobRunner
from app.runners.factory import build_job_runner
from app.runners.null_runner import NullJobRunner
from app.runners.protocols import (
    JobDispatch,
    JobDispatcher,
    JobLogSource,
    JobRunner,
    LogEntry,
)

__all__ = [
    "CompositeJobRunner",
    "JobDispatch",
    "JobDispatcher",
    "JobLogSource",
    "JobRunner",
    "LogEntry",
    "NullJobRunner",
    "build_job_runner",
]
