# app/utils/logs.py
from __future__ import annotations

from typing import cast

import logfire
from logfire import CodeSource, LevelName
from logfire.types import ExceptionCallbackHelper
from starlette.exceptions import HTTPException

from app.config import get_config, get_logfire_config


def quiet_client_errors(helper: ExceptionCallbackHelper) -> None:
    """Keep client errors out of Logfire's exception stream.

    Logfire's FastAPI instrumentation records every exception that leaves an
    endpoint or a dependency on the request span, including an ``HTTPException``
    that FastAPI then turns into an ordinary 4xx response. Recorded exceptions
    open Logfire issues, so a 404 for an unknown resource would page like a
    server fault. A status below 500 is the caller's to fix, so its exception
    is not recorded; the request span itself is still exported. A ``>= 500``
    ``HTTPException`` and every other exception are recorded as before.

    Covers ``fastapi.HTTPException`` too, which subclasses Starlette's.

    Args:
        helper (ExceptionCallbackHelper): Logfire's view of the exception being
            recorded.
    """
    exc = helper.exception
    if isinstance(exc, HTTPException) and exc.status_code < 500:
        helper.no_record_exception()


def setup_logging() -> None:
    lf = get_logfire_config()
    cfg = get_config()
    logfire.configure(
        environment=cfg.env,
        min_level=cast(LevelName, lf.log_level),
        send_to_logfire="if-token-present",
        service_name="media-api",
        code_source=CodeSource(repository=lf.code_source_repository, revision="HEAD"),
        console=logfire.ConsoleOptions(
            include_timestamps=False, min_log_level=cast(LevelName, lf.console_log_level)
        ),
        advanced=logfire.AdvancedOptions(exception_callback=quiet_client_errors),
    )
