# app/orchestration/providers.py
"""The provider contract every orchestration adapter implements.

A ``transform_type`` such as ``prefect.transcode`` is a provider-qualified
routing key: everything before the first ``.`` selects the adapter, and
everything after -- the provider-local command -- stays opaque to the core
API (e.g. ``kubernetes.ffmpeg/transcode``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.runners.protocols import JobDispatch, LogEntry

PROVIDER_API_VERSION = 1


@dataclass(frozen=True)
class TransformRoute:
    provider: str
    command: str

    @classmethod
    def parse(cls, transform_type: str) -> TransformRoute:
        provider, separator, command = transform_type.partition(".")
        if not separator or not provider or not command:
            raise ValueError("transform_type must use '<provider>.<provider-local-type>' format")
        return cls(provider=provider, command=command)


@runtime_checkable
class OrchestrationProvider(Protocol):
    key: str
    api_version: int

    def dispatch(self, route: TransformRoute, job: JobDispatch) -> None: ...

    def fetch_logs(self, route: TransformRoute, external_job_id: str) -> list[LogEntry]: ...
