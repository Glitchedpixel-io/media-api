from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable

from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

# Statuses for which HTTP forbids a message body (RFC 9110 §6.4.1). An
# HTTPException raised with one of these must not carry a JSON body onward,
# even though `exc.detail` is set.
_NO_BODY_STATUS_CODES = {204, 304}


class QuietClientErrorRoute(APIRoute):
    """API route that returns client-error responses instead of raising them.

    Logfire's FastAPI instrumentation records *every* exception that propagates
    out of the endpoint function as an exception event on the request span — and
    that happens deep inside ``run_endpoint_function``, before any
    ``app.exception_handler`` runs, so handlers cannot suppress it. A bare
    ``raise HTTPException(404, ...)`` for an unknown resource therefore shows up
    in Logfire's issues as if it were a server-side fault.

    Such 4xx conditions are the caller's to address, not ours. This route wraps
    the endpoint callable so that an ``HTTPException`` with a client-error status
    (``< 500``) is converted to the equivalent response and *returned* rather
    than raised. Returning short-circuits the instrumentation, so no exception
    is recorded, while the response the client receives — status, body and
    headers — is identical to FastAPI's default handler: a ``{"detail": ...}``
    :class:`JSONResponse` for most codes, or a bodyless :class:`Response` for
    statuses where HTTP forbids a body (204, 304) — Starlette's ``JSONResponse``
    does not strip the body for these on its own, so passing ``exc.detail``
    through unconditionally would put an invalid body on the wire. Server
    errors (``>= 500``) are re-raised unchanged so genuine faults are still
    recorded.

    FastAPI decides once, at route-construction time, whether to await the
    endpoint directly or run it in a threadpool, based on whether the *original*
    callable is a coroutine function. The wrapper must preserve that: replacing a
    sync endpoint with an ``async def`` wrapper leaves the coroutine it returns
    un-awaited (FastAPI still dispatches it via the threadpool), and the caller
    silently gets back a coroutine object instead of the response.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        endpoint = self.dependant.call
        if endpoint is None:  # pragma: no cover
            raise RuntimeError(f"Route {self.path!r} has no endpoint callable")

        quiet_endpoint: Callable[..., Any]
        if asyncio.iscoroutinefunction(endpoint):

            @functools.wraps(endpoint)
            async def quiet_endpoint(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await endpoint(*args, **kwargs)
                except HTTPException as exc:
                    if exc.status_code >= 500:
                        raise
                    if exc.status_code in _NO_BODY_STATUS_CODES:
                        return Response(status_code=exc.status_code, headers=exc.headers)
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers,
                    )

        else:

            @functools.wraps(endpoint)
            def quiet_endpoint(*args: Any, **kwargs: Any) -> Any:
                try:
                    return endpoint(*args, **kwargs)
                except HTTPException as exc:
                    if exc.status_code >= 500:
                        raise
                    if exc.status_code in _NO_BODY_STATUS_CODES:
                        return Response(status_code=exc.status_code, headers=exc.headers)
                    return JSONResponse(
                        status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=exc.headers,
                    )

        self.dependant.call = quiet_endpoint
