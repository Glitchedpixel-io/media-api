# app/orchestration/registry.py
"""Resolves dispatch and log requests to the enabled orchestration provider.

Routing is carried entirely by each request's own ``transform_type`` -- there
is no config-driven routing map. A request whose provider prefix has no
matching enabled provider is treated as an observable no-op (dispatch) or an
empty result (logs), never a failure: only *enabling* a provider is a
startup-time concern (see :mod:`app.orchestration.loader`).
"""

from __future__ import annotations

import logging

from app.orchestration.providers import OrchestrationProvider, TransformRoute
from app.runners.protocols import JobDispatch, LogEntry

logger = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, providers: list[OrchestrationProvider]) -> None:
        self._providers: dict[str, OrchestrationProvider] = {}
        for provider in providers:
            key = provider.key.lower()
            if key in self._providers:
                raise ValueError(f"Duplicate orchestration provider key: {key!r}")
            self._providers[key] = provider

    def dispatch(self, job: JobDispatch) -> None:
        try:
            route = TransformRoute.parse(job.job_type)
        except ValueError:
            logger.info("No orchestration route for transform_type %r", job.job_type)
            return
        provider = self._providers.get(route.provider.lower())
        if provider is None:
            logger.info(
                "No orchestration provider enabled for %r (transform_type=%r)",
                route.provider,
                job.job_type,
            )
            return
        provider.dispatch(route, job)

    def fetch_logs(self, transform_type: str, external_job_id: str) -> list[LogEntry]:
        try:
            route = TransformRoute.parse(transform_type)
        except ValueError:
            return []
        provider = self._providers.get(route.provider.lower())
        if provider is None:
            return []
        return provider.fetch_logs(route, external_job_id)


# Global instance to be initialized during app startup
_provider_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    if _provider_registry is None:
        raise RuntimeError("ProviderRegistry not initialized. Call init_provider_registry() first.")
    return _provider_registry


def init_provider_registry(registry: ProviderRegistry) -> ProviderRegistry:
    global _provider_registry  # noqa: PLW0603
    _provider_registry = registry
    return _provider_registry
