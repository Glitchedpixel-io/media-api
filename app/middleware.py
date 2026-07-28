# app/middleware.py
"""
Middleware for FastAPI application.
Includes request ID tracking for distributed tracing and log correlation.
"""

from __future__ import annotations

import logfire
import uuid
from collections.abc import Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable to store request ID for the current request
# This can be accessed from anywhere in the application during request processing
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return request_id_ctx.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds a unique request ID to each request.

    - Checks for existing X-Request-ID header (for distributed tracing)
    - Generates new UUID if not present
    - Stores in context variable for access throughout request lifecycle
    - Adds X-Request-ID header to response
    - Logs request details with request ID
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Check if request already has an ID (from upstream service/load balancer)
        request_id = request.headers.get("X-Request-ID")

        # Generate new ID if not present
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store in context variable for access in loggers/services
        request_id_ctx.set(request_id)

        # Add to request state for easy access in route handlers
        request.state.request_id = request_id

        with logfire.span(f"request {request_id}") as span:

            # Process request - logfire will capture the exeception details automatically
            # ...but won't consume it, which is what we need
            response = None
            try:
                response = await call_next(request)
            finally:
                # Add request ID to response headers for client tracing
                if response is not None:
                    response.headers["X-Request-ID"] = request_id

        return response  # type: ignore
