# app/main.py
from __future__ import annotations
import logfire

from .config import get_config
from .utils.logs import setup_logging

from app.app_factory import create_app

setup_logging()

api = create_app(get_config())

# Backwards-compatible alias for typical ASGI loaders expecting `app`
app = api

logfire.info("API application initialized via app_factory.create_app()")
