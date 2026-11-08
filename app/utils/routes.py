# app/utils/routes.py
from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts


def api_routes(app: FastAPI) -> Iterator[RouteContext]:
    """Yield every path operation the app serves, as FastAPI resolves it.

    From FastAPI 0.137, ``app.routes`` is a tree: an included router appears as one
    intermediate node rather than as copies of its routes, so filtering
    ``app.routes`` for ``APIRoute`` silently misses everything added with
    ``include_router``. ``iter_route_contexts`` is FastAPI's supported way to walk
    it. Each context reports the *effective* route -- the path with its router
    prefixes, and dependencies, tags and responses merged from every enclosing
    router -- while ``original_route`` is the route object as declared.

    Args:
        app (FastAPI): The application, with its routers already included.

    Yields:
        RouteContext: One per path operation, websocket and mount routes excluded.
    """
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute):
            yield context
