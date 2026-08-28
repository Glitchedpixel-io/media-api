"""Multipart file fields are described in a way code generators actually read.

FastAPI emits OpenAPI 3.1, where an upload is `contentMediaType:
application/octet-stream`. openapi-python-client -- which media-runners generates its
vendored client from -- keys file uploads off 3.0's `format: binary` and ignores
`contentMediaType` entirely. Given only the 3.1 spelling it does not fail: it types the
field `str` and emits a `to_multipart()` that posts the value as a `text/plain` part.

So `POST /api/assets/{id}/artwork` was callable-looking and uncallable. The generated
client sent the *path string* where the image belonged, and the store's magic-number
sniffing refused it as 415 -- discovered when media-runners-acquisition went to upload
covers at acquisition time rather than waiting on the artwork backfill.

The generator is not in this repo's test environment, so these assert the property the
generator needs rather than driving it. Reproduced against openapi-python-client 0.26.2
(the pinned version) and 0.29.0 (latest): both produce `file: str` without `format`,
and 0.26.2 produces the correct `file: File` with it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.unit, pytest.mark.api]

OCTET_STREAM = "application/octet-stream"

#: The upload bodies as they stand. Named so a failure says which endpoint regressed,
#: but `test_every_binary_property_is_generator_readable` is the guard that also
#: covers an upload endpoint added after this was written.
UPLOAD_BODIES = ("Body_upload_asset_artwork", "Body_upload_title_artwork")


def _binary_properties(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every schema property describing raw uploaded bytes, keyed "Schema.property"."""
    found: dict[str, dict[str, Any]] = {}
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        for prop_name, prop in (schema.get("properties") or {}).items():
            if prop.get("contentMediaType") == OCTET_STREAM or prop.get("format") == "binary":
                found[f"{name}.{prop_name}"] = prop
    return found


def test_every_binary_property_is_generator_readable(api_app: FastAPI) -> None:
    """The guard that matters: no upload is described in 3.1's spelling alone.

    Written over the whole spec rather than the two known bodies so that an upload
    endpoint added later is covered the day it appears, without anyone remembering
    to extend a list here.
    """
    binary = _binary_properties(api_app.openapi())

    assert binary, "no binary properties found at all -- has the upload surface moved?"

    missing = sorted(key for key, prop in binary.items() if prop.get("format") != "binary")
    assert not missing, (
        "these upload fields carry only OpenAPI 3.1's contentMediaType, which "
        f"openapi-python-client does not read, so it will type them `str`: {missing}"
    )


@pytest.mark.parametrize("body", UPLOAD_BODIES)
def test_artwork_upload_bodies_declare_a_binary_file(api_app: FastAPI, body: str) -> None:
    """The two endpoints the incident was actually about."""
    schemas = api_app.openapi()["components"]["schemas"]

    assert body in schemas, f"{body} is gone from the spec"
    file_prop = schemas[body]["properties"]["file"]

    assert file_prop["format"] == "binary"
    # Added alongside, never in place of: 3.1 keeps `format` as an annotation, and a
    # consumer that reads contentMediaType correctly must not lose it to this fix.
    assert file_prop["contentMediaType"] == OCTET_STREAM


def test_annotating_is_idempotent(api_app: FastAPI) -> None:
    """`openapi()` is called more than once in a process, and memoises.

    FastAPI caches into `openapi_schema` and returns the same dict every time, so the
    hook annotates a document it has already annotated. That must be a no-op rather
    than compounding.
    """
    first = api_app.openapi()
    second = api_app.openapi()

    assert first == second
    assert _binary_properties(second).keys() == _binary_properties(first).keys()


def test_non_upload_string_fields_are_left_alone(api_app: FastAPI) -> None:
    """The fix must not scatter `format: binary` across ordinary strings.

    A blunter implementation keyed on "is a string in a multipart body" would mark
    `artwork_kind` too, and the generator would then expect bytes for a form field.
    """
    upload_form = api_app.openapi()["components"]["schemas"]["Body_upload_asset_artwork"]
    kind = upload_form["properties"]["artwork_kind"]

    assert "format" not in kind
    assert kind["type"] == "string"
