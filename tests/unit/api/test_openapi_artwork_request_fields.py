"""No artwork request body may carry a field the server establishes for itself.

`storage_path`, `mime`, `width` and `height` are determined from the uploaded bytes:
the store sniffs the format from magic numbers and measures the image itself, because
a filename, a Content-Type and a declared size are all things the caller controls. A
request schema that accepts any of them hands that decision back.

#139 removed them from PATCH and #141 from the upload form. This is what keeps them
out. `ArtworkAttrs` is the base of several models, so a field added there reaches a
request schema without anyone intending it -- a convention in CLAUDE.md would not
notice, and the OpenAPI document is where the actual contract is visible.

**Scoped to artwork paths, deliberately.** `StreamCreatePublic` and
`StreamPatchPublic` carry `width` and `height` legitimately: a video stream's
dimensions come from ffprobe and are submitted by a runner, which is the opposite
situation. A rule written over every request body in the API would be wrong rather
than merely noisy.

Response schemas are untouched by this: `ArtworkRead` should absolutely carry all
four. The rule is about what a client may *send*.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.unit, pytest.mark.api]

#: Fields the server discovers from the bytes. A client may read these, never send them.
SERVER_DISCOVERED = ("width", "height", "storage_path", "mime")

_REF_PREFIX = "#/components/schemas/"


def _reachable_schemas(spec: dict[str, Any], node: Any, seen: set[str] | None = None) -> set[str]:
    """Every component schema name reachable from a schema node.

    Follows ``$ref`` as well as the composition keywords, so a guarded field nested
    inside an ``allOf`` branch or a list item is found rather than missed.

    Args:
        spec: The whole OpenAPI document.
        node: A schema node to walk.
        seen: Names already visited, which also breaks recursive schemas.

    Returns:
        set[str]: Names of the component schemas reachable from ``node``.
    """
    seen = set() if seen is None else seen
    if isinstance(node, list):
        for item in node:
            _reachable_schemas(spec, item, seen)
        return seen
    if not isinstance(node, dict):
        return seen

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref[len(_REF_PREFIX) :]
        if name not in seen:
            seen.add(name)
            _reachable_schemas(spec, spec["components"]["schemas"].get(name, {}), seen)
        return seen

    for key in ("allOf", "anyOf", "oneOf", "items", "additionalProperties", "not"):
        if key in node:
            _reachable_schemas(spec, node[key], seen)
    for prop in (node.get("properties") or {}).values():
        _reachable_schemas(spec, prop, seen)
    return seen


def _artwork_request_schemas(spec: dict[str, Any]) -> dict[str, set[str]]:
    """Schema name -> the artwork operations that accept it as a request body."""
    found: dict[str, set[str]] = {}
    for path, operations in spec.get("paths", {}).items():
        if "artwork" not in path:
            continue
        for method, operation in operations.items():
            body = operation.get("requestBody") if isinstance(operation, dict) else None
            if not body:
                continue
            for media in body.get("content", {}).values():
                for name in _reachable_schemas(spec, media.get("schema", {})):
                    found.setdefault(name, set()).add(f"{method.upper()} {path}")
    return found


def test_no_artwork_request_body_accepts_a_server_discovered_field(api_app: FastAPI) -> None:
    """The guard. Written over the whole artwork surface rather than a list of known
    schemas, so an artwork write endpoint added later is covered the day it appears.
    """
    spec = api_app.openapi()
    schemas = spec["components"]["schemas"]
    request_schemas = _artwork_request_schemas(spec)

    assert request_schemas, "no artwork request bodies found at all -- has the surface moved?"

    offences = [
        f"{name}.{field} (accepted by {', '.join(sorted(operations))})"
        for name, operations in sorted(request_schemas.items())
        for field in SERVER_DISCOVERED
        if field in (schemas.get(name, {}).get("properties") or {})
    ]

    assert not offences, (
        "artwork request bodies must not accept a field the server establishes from "
        "the uploaded bytes -- the store sniffs the mime and measures the image "
        "precisely because the caller controls what it sends, and accepting one of "
        f"these hands that decision back (#139, #141): {offences}"
    )


def test_the_guard_covers_the_endpoints_it_is_meant_to(api_app: FastAPI) -> None:
    """A scoping bug that quietly matched nothing would make the test above vacuous
    while still passing, so pin the operations it is actually reaching."""
    covered = {
        operation
        for operations in _artwork_request_schemas(api_app.openapi()).values()
        for operation in operations
    }

    assert covered >= {
        "PATCH /api/artwork/{artwork_id}",
        "POST /api/titles/{title_id}/artwork",
        "POST /api/assets/{asset_id}/artwork",
    }


def test_the_read_model_still_reports_all_four(api_app: FastAPI) -> None:
    """The rule is about sending, not reading. A client cannot lay out an image it
    cannot get the dimensions of, so ArtworkRead losing these would be a regression
    in the opposite direction."""
    properties = api_app.openapi()["components"]["schemas"]["ArtworkRead"]["properties"]

    assert all(field in properties for field in SERVER_DISCOVERED)
