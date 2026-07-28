from __future__ import annotations

import logfire
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, Callable

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.auth.jwt import get_current_user
from app.config import AppConfig, get_config, get_version, init_config
from app.database import assert_database_timezone_utc, get_engine, init_db
from app.elasticsearch_client import close_es, get_es_manager, initialize_es
from app.middleware import RequestIdMiddleware
from app.routers.assets import router as assets_router
from app.routers.external_ids import router as external_ids_router
from app.routers.file_stream import router as file_stream_router
from app.routers.id_schemes import router as id_schemes_router
from app.routers.inbox import router as inbox_router
from app.routers.jobs import router as jobs_router
from app.routers.logs import router as logs_router
from app.routers.run_summaries import router as run_summaries_router
from app.routers.runner_state import router as runner_state_router
from app.routers.scanner_run_summaries import router as scanner_run_summaries_router
from app.routers.search_transcripts import router as search_router
from app.routers.streams import router as streams_router
from app.routers.tags import router as tags_router
from app.routers.titles import router as titles_router
from app.routers.transform_requests import router as transform_requests_router


def get_lifespan(config: AppConfig | None = None) -> Callable[[FastAPI], AsyncContextManager[None]]:
    """Create a lifespan context manager, optionally using an injected AppConfig."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        cfg = config or get_config()

        # Startup
        logfire.info("Starting up application—initializing database and external services")
        try:
            init_db(cfg.database)
            # Schema is owned by Alembic migrations (run as a deploy step), not
            # created at startup. See docs/ and alembic/env.py.
            assert_database_timezone_utc(get_engine())
            logfire.info("Database initialized")
        except Exception as ex:  # pragma: no cover - defensive logging only
            logfire.exception(f"Database initialization failed: {ex}")

        # Initialize Elasticsearch client
        try:
            initialize_es(cfg.elasticsearch)
            logfire.info("Elasticsearch client initialized")
        except Exception as ex:  # pragma: no cover - defensive logging only
            logfire.exception(f"Elasticsearch initialization failed: {ex}")

        yield

        # Shutdown
        logfire.info("Shutting down application—disposing resources")

        # Close Elasticsearch client
        try:
            close_es()
            logfire.info("Elasticsearch client closed")
        except Exception as ex:  # pragma: no cover - defensive logging only
            logfire.warning(f"Elasticsearch shutdown error: {ex}")

        # Dispose database engine
        try:
            get_engine().dispose()
            logfire.info("Database engine disposed")
        except Exception as ex:  # pragma: no cover - defensive logging only
            logfire.warning(f"Database shutdown error: {ex}")

    return lifespan


def _include_routers(api: FastAPI) -> None:
    """Register API routers. Kept in a single place for clarity."""
    api.include_router(
        titles_router,
        prefix="/api/titles",
        tags=["titles"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        assets_router,
        prefix="/api/assets",
        tags=["assets"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        streams_router,
        prefix="/api/streams",
        tags=["streams"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        file_stream_router,
        prefix="/api/fetch",
        tags=["stream"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        transform_requests_router,
        prefix="/api/transform_requests",
        tags=["transform_requests"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        run_summaries_router,
        prefix="/api/run_summaries",
        tags=["run_summaries"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        scanner_run_summaries_router,
        prefix="/api/scanner_run_summaries",
        tags=["run_summaries"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        search_router,
        prefix="/api/search",
        tags=["search"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        tags_router,
        prefix="/api/tags",
        tags=["tags"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        inbox_router,
        prefix="/api/inbox",
        tags=["inbox"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        runner_state_router,
        prefix="/api/runner_state",
        tags=["runners"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        id_schemes_router,
        prefix="/api/id_schemes",
        tags=["id", "assets"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        external_ids_router,
        prefix="/api/external-ids",
        tags=["external-ids"],
        dependencies=[Depends(get_current_user)],
    )
    api.include_router(
        logs_router,
        prefix="/api/log",
        tags=["logging"],
    )
    api.include_router(
        jobs_router,
        prefix="/api/jobs",
        tags=["jobs"],
        dependencies=[Depends(get_current_user)],
    )


def create_app(config: AppConfig, allow_origins: Sequence[str] | None = None) -> FastAPI:
    """Application factory returning a configured FastAPI instance."""

    init_config(config)
    logfire.info(f"Environment loaded {config.env}")

    api = FastAPI(
        lifespan=get_lifespan(config),
        title="Media API",
        description="Media Library Management API",
    )

    origins = tuple(allow_origins) if allow_origins is not None else config.cors_origins

    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "traceparent",
            "tracestate",
        ],
    )

    # Add request ID middleware for tracing and log correlation
    api.add_middleware(RequestIdMiddleware)

    @api.get("/api/ping", operation_id="ping")
    def ping() -> dict[str, str]:
        return {"pong": "pong"}

    @api.get("/api/version", operation_id="get_version")
    def read_version() -> dict[str, str]:
        return {
            "version": get_version(),
            "log-level": config.logfire.log_level,
            "console-level": config.logfire.console_log_level,
        }

    @api.get("/api/health", operation_id="get_health")
    def health_check() -> dict[str, Any]:
        health_status: dict[str, Any] = {
            "status": "healthy",
            "version": get_version(),
            "services": {},
        }

        # Check database connectivity
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            health_status["services"]["database"] = {
                "status": "healthy",
                "type": get_engine().dialect.name,
            }
        except Exception as e:
            health_status["status"] = "degraded"
            health_status["services"]["database"] = {
                "status": "unhealthy",
                "error": str(e),
            }

        # Check Elasticsearch connectivity
        try:
            es_manager = get_es_manager()
            es_health = es_manager.health_check()
            health_status["services"]["elasticsearch"] = es_health
            if not es_health.get("healthy", False):
                health_status["status"] = "degraded"
        except Exception as e:
            health_status["services"]["elasticsearch"] = {
                "status": "unavailable",
                "healthy": False,
                "error": str(e),
            }

        return health_status

    _include_routers(api)

    # ensure the app is instrumented for logging
    logfire.instrument_fastapi(api)

    return api
