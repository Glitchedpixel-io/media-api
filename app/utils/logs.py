# app/utils/logs.py
from __future__ import annotations

from typing import cast

import logfire
from logfire import CodeSource, LevelName

from app.config import get_config, get_logfire_config


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
    )
