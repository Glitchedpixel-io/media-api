# app/orchestration/loader.py
"""Discovers and instantiates enabled orchestration providers at startup.

Providers are discovered from the ``media_api.orchestration_providers`` Python
entry-point group. Only providers named in ``OrchestrationConfig.enabled_providers``
are ever imported or instantiated -- request content never selects a provider
to import. Every failure mode here is meant to fail startup clearly rather
than surface as a mysterious mid-request error.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from app.config.schema import OrchestrationConfig
from app.orchestration.providers import PROVIDER_API_VERSION, OrchestrationProvider
from app.orchestration.registry import ProviderRegistry

ENTRY_POINT_GROUP = "media_api.orchestration_providers"


def build_provider_registry(config: OrchestrationConfig) -> ProviderRegistry:
    discovered = {ep.name: ep for ep in entry_points(group=ENTRY_POINT_GROUP)}

    providers: list[OrchestrationProvider] = []
    for name in config.enabled_providers:
        entry_point = discovered.get(name)
        if entry_point is None:
            raise ValueError(
                f"Orchestration provider {name!r} is enabled but no entry point is "
                f"registered for it in the {ENTRY_POINT_GROUP!r} group. Installed "
                f"providers: {sorted(discovered)}"
            )

        provider_cls = entry_point.load()
        options = config.provider_options.get(name, {})
        try:
            provider = provider_cls(**options)
        except TypeError as e:
            raise ValueError(
                f"Failed to construct orchestration provider {name!r} with options "
                f"{dict(options)!r}: {e}"
            ) from e

        if provider.api_version != PROVIDER_API_VERSION:
            raise ValueError(
                f"Orchestration provider {name!r} declares api_version="
                f"{provider.api_version!r}, but media-api requires api_version="
                f"{PROVIDER_API_VERSION!r}."
            )

        providers.append(provider)

    return ProviderRegistry(providers)
