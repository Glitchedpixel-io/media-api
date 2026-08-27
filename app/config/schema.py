from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    pool_size: int = 5
    logfire_for_sqlalchemy: bool = False


@dataclass(frozen=True)
class MediaConfig:
    """The filesystem roots this service reads from and writes to.

    ``artwork_root`` is content-addressed rather than keyed by entity: a season
    and its episodes routinely share one poster, and there is no title-side
    equivalent of ``accessory_relative_path`` to key it by. See
    ``app.utils.paths.artwork_relative_path``.
    """

    media_root: str
    accessory_root: str
    inbox_root: str
    artwork_root: str


@dataclass(frozen=True)
class ElasticsearchConfig:
    url: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    insecure: bool = False
    ca_cert: str | None = None
    transcripts_index: str = "transcript-segments"


@dataclass(frozen=True)
class OrchestrationConfig:
    """Selects and configures the enabled orchestration providers.

    An empty ``enabled_providers`` (the default) is the pure pull model -- no
    orchestration framework is required to boot. Dispatch/log routing is
    carried entirely by each request's own ``transform_type`` (a
    provider-qualified key, e.g. ``prefect.transcode``), not by config --
    there is no routing map to configure here. ``provider_options`` carries
    provider-scoped construction kwargs (e.g. ``{"webhook": {"url": "..."}}"``)
    so enabling a new integration never requires new core settings.
    """

    enabled_providers: tuple[str, ...] = ()
    provider_options: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthConfig:
    oidc_issuer: str = ""
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_algorithms: str = "RS256"
    # Bypasses JWT verification with a stub principal. Local dev only —
    # refused at settings load time when APP_ENV=production.
    disabled: bool = False


@dataclass(frozen=True)
class LogfireConfig:
    token: str = ""
    base_url: str = "https://logfire-eu.pydantic.dev"
    log_level: str = "info"
    console_log_level: str = "info"
    code_source_repository: str = ""


@dataclass(frozen=True)
class AppConfig:
    debug: bool
    env: str
    version: str
    database: DatabaseConfig
    media: MediaConfig
    elasticsearch: ElasticsearchConfig
    orchestration: OrchestrationConfig
    auth: AuthConfig
    logfire: LogfireConfig
    # Local-dev defaults only. Production origins are injected via CORS_ORIGINS;
    # never hardcode a deployment hostname here.
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
