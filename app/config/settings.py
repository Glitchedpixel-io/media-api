from __future__ import annotations

import json
import os
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import MappingProxyType
from typing import Annotated

from logfire import LevelName
from pydantic import AliasChoices, Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import DotEnvSettingsSource

from app.config.schema import (
    AppConfig,
    AuthConfig,
    DatabaseConfig,
    ElasticsearchConfig,
    LogfireConfig,
    MediaConfig,
    OrchestrationConfig,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DIST_NAME = "media-api"


def get_version() -> str:
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0.0.0+local"


class _Settings(BaseSettings):
    env: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development").lower())
    log_level: LevelName = Field("info", description="Logging level")
    console_log_level: LevelName = Field("info", description="Console logging level")
    logfire_token: str = Field("", description="Logfire token")
    logfire_base_url: str = Field("https://logfire-eu.pydantic.dev", description="Logfire base URL")
    logfire_for_sqlalchemy: bool = Field(False, description="Log SQLAlchemy queries")
    logfire_code_source_repository: str = Field(
        "",
        description="Repository URL used for Logfire code-source links",
    )
    # NoDecode: keep pydantic-settings from JSON-decoding the env value so the
    # validator below can parse a plain comma-separated string.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Allowed CORS origins; set a comma-separated list per environment",
    )
    version: str = Field(get_version(), description="Application version")
    database_url: PostgresDsn = Field(
        PostgresDsn(url="postgresql+psycopg://user:secret@localhost:5432/media_dev"),
        validation_alias=AliasChoices("TEST_DATABASE_URL", "DATABASE_URL"),
    )
    database_pool_size: int = Field(5, description="SQLAlchemy connection pool size")
    debug: bool = False

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, v: str | LevelName) -> str:
        if isinstance(v, str):
            return v.lower()
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Accept a comma-separated string (e.g. from an env var) or a list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("enabled_orchestration_providers", mode="before")
    @classmethod
    def split_enabled_orchestration_providers(cls, v: str | list[str]) -> list[str]:
        """Accept a comma-separated string (e.g. from an env var) or a list."""
        if isinstance(v, str):
            return [provider.strip() for provider in v.split(",") if provider.strip()]
        return v

    @field_validator("orchestration_provider_options", mode="before")
    @classmethod
    def parse_orchestration_provider_options(
        cls, v: str | dict[str, dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        """Accept a JSON object string (e.g. from an env var) or a dict; blank means none."""
        if isinstance(v, str):
            if not v.strip():
                return {}
            return json.loads(v)
        return v

    @model_validator(mode="after")
    def forbid_auth_disabled_in_production(self) -> _Settings:
        if self.auth_disabled and self.env == "production":
            raise ValueError("AUTH_DISABLED must not be set when APP_ENV=production")
        return self

    # OIDC / Auth (Keycloak-shaped defaults — JWKS path convention and
    # realm_access/resource_access claim extraction in app/auth/jwt.py — but
    # any standard OIDC provider works)
    oidc_issuer: str = Field("https://id.example.com/realms/RealmName")
    oidc_audience: str | None = Field(None)
    oidc_jwks_url: str | None = Field(None)
    oidc_algorithms: str = Field("RS256")
    auth_disabled: bool = Field(
        False,
        description="Bypass JWT verification with a stub principal. Local dev only.",
    )

    # Filesystem roots
    media_root: str = Field(str(BASE_DIR / "media"))
    accessory_root: str = Field(str(BASE_DIR / "accessory-store"))
    inbox_root: str = Field(str(BASE_DIR / "inbox"))

    # Elasticsearch
    elasticsearch_url: str | None = Field(None)
    es_username: str | None = Field(None)
    es_password: str | None = Field(None)
    es_api_key: str | None = Field(None)
    es_insecure: bool = Field(False)
    es_ca_cert: str | None = Field(None)
    transcripts_index: str = Field("transcript-segments")

    # Orchestration provider selection. Default is empty -- the pure pull
    # model, no orchestration framework required to boot. Providers are
    # discovered via the media_api.orchestration_providers entry-point group
    # and only enabled ones are ever instantiated. Routing is carried by each
    # request's own transform_type, not by config -- there is no routing map
    # here, only provider-scoped construction options.
    # NoDecode: keep pydantic-settings from JSON-decoding the env value so the
    # validator below can parse a plain comma-separated string.
    enabled_orchestration_providers: Annotated[list[str], NoDecode] = Field(
        default=[],
        description="Enabled orchestration provider keys, comma-separated (e.g. 'prefect,webhook')",
    )
    # NoDecode: an unset/blank env var isn't valid JSON -- decode it ourselves
    # below so blank means "no options" instead of a startup crash.
    orchestration_provider_options: Annotated[dict[str, dict[str, object]], NoDecode] = Field(
        default_factory=dict,
        description=(
            "Provider-scoped construction options as a JSON object, e.g. "
            '{"webhook": {"url": "https://example.com/hook"}}'
        ),
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        app_env = os.getenv("APP_ENV", "development").lower()
        dotenv_settings = DotEnvSettingsSource(
            settings_cls,
            env_file=(
                BASE_DIR / ".env",
                BASE_DIR / f".env.{app_env}",
                BASE_DIR / ".env.local",
                BASE_DIR / f".env.{app_env}.local",
            ),
            env_file_encoding="utf-8",
        )
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _build_database_config(s: _Settings) -> DatabaseConfig:
    return DatabaseConfig(
        url=str(s.database_url),
        pool_size=s.database_pool_size,
        logfire_for_sqlalchemy=s.logfire_for_sqlalchemy,
    )


def _build_media_config(s: _Settings) -> MediaConfig:
    return MediaConfig(
        media_root=s.media_root,
        accessory_root=s.accessory_root,
        inbox_root=s.inbox_root,
    )


def _build_elasticsearch_config(s: _Settings) -> ElasticsearchConfig:
    return ElasticsearchConfig(
        url=s.elasticsearch_url,
        username=s.es_username,
        password=s.es_password,
        api_key=s.es_api_key,
        insecure=s.es_insecure,
        ca_cert=s.es_ca_cert,
        transcripts_index=s.transcripts_index,
    )


def _build_orchestration_config(s: _Settings) -> OrchestrationConfig:
    return OrchestrationConfig(
        enabled_providers=tuple(s.enabled_orchestration_providers),
        provider_options=MappingProxyType(
            {
                name: MappingProxyType(options)
                for name, options in s.orchestration_provider_options.items()
            }
        ),
    )


def _build_auth_config(s: _Settings) -> AuthConfig:
    return AuthConfig(
        oidc_issuer=s.oidc_issuer,
        oidc_audience=s.oidc_audience,
        oidc_jwks_url=s.oidc_jwks_url,
        oidc_algorithms=s.oidc_algorithms,
        disabled=s.auth_disabled,
    )


def _build_logfire_config(s: _Settings) -> LogfireConfig:
    return LogfireConfig(
        token=s.logfire_token,
        base_url=s.logfire_base_url,
        log_level=s.log_level,
        console_log_level=s.console_log_level,
        code_source_repository=s.logfire_code_source_repository,
    )


def _load() -> AppConfig:
    s = _Settings()
    return AppConfig(
        debug=s.debug,
        env=s.env,
        version=s.version,
        database=_build_database_config(s),
        media=_build_media_config(s),
        elasticsearch=_build_elasticsearch_config(s),
        orchestration=_build_orchestration_config(s),
        auth=_build_auth_config(s),
        logfire=_build_logfire_config(s),
        cors_origins=tuple(s.cors_origins),
    )


_current_config: AppConfig | None = None


def init_config(config: AppConfig) -> None:
    """Override the cached config (for app factory or programmatic setup)."""
    global _current_config  # noqa: PLW0603
    _current_config = config
    get_config.cache_clear()


@lru_cache
def get_config() -> AppConfig:
    if _current_config is not None:
        return _current_config
    return _load()


# Per-group accessors — correct Depends targets in routers and service factories.
# No individual @lru_cache; the cache belongs only on get_config().


def get_db_config() -> DatabaseConfig:
    return get_config().database


def get_media_config() -> MediaConfig:
    return get_config().media


def get_es_config() -> ElasticsearchConfig:
    return get_config().elasticsearch


def get_orchestration_config() -> OrchestrationConfig:
    return get_config().orchestration


def get_auth_config() -> AuthConfig:
    return get_config().auth


def get_logfire_config() -> LogfireConfig:
    return get_config().logfire
