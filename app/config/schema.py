from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    pool_size: int = 5
    logfire_for_sqlalchemy: bool = False


@dataclass(frozen=True)
class MediaConfig:
    media_root: str
    accessory_root: str
    inbox_root: str


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
class RunnerConfig:
    """Selects and configures the job-execution backend.

    ``backend="none"`` (the default) is the pure pull model -- no framework is
    required. Routing is carried by each request's own ``transform_type``
    (a provider-qualified key, e.g. ``prefect.transcode``), not by config --
    there is no map to configure here. ``webhook_url`` is used by the webhook
    backend. This replaces the old Prefect-specific config: the former
    ``run_on_demand=false`` is simply ``backend="none"``.
    """

    backend: str = "none"
    webhook_url: str | None = None


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
    runner: RunnerConfig
    auth: AuthConfig
    logfire: LogfireConfig
    # Local-dev defaults only. Production origins are injected via CORS_ORIGINS;
    # never hardcode a deployment hostname here.
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
