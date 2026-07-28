"""
Common API response definitions for FastAPI endpoints.

This module provides reusable response documentation that can be composed
and extended at the endpoint level to ensure consistency across the API.
"""

from typing import Any

# Common responses that apply to write operations (POST, PATCH, PUT, DELETE)
COMMON_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {"description": "Conflict - unique constraint violated or relationship not permitted"},
    422: {
        "description": "Unprocessable Entity - validation error or database integrity constraint violated",
        "content": {"application/json": {"example": {"detail": "Validation error details here"}}},
    },
    423: {"description": "Locked - database is currently in read-only mode"},
}

# Common responses for read operations (GET)
COMMON_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"description": "Not Found - the requested resource does not exist"},
}

# All common responses combined (for endpoints that need both read and write)
COMMON_RESPONSES: dict[int | str, dict[str, Any]] = {
    **COMMON_READ_RESPONSES,
    **COMMON_WRITE_RESPONSES,
}
