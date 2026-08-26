"""Phase 1 -- the static surface, read from the app object.

The OpenAPI document is generated in process via ``app.openapi()``. No server
needs to be running and no database needs to be reachable: the app factory is
called with a config whose defaults resolve without any environment at all.

The document is the skeleton, not the answer. Two things it does not say are
recovered here by inspecting the live route table and the SQLAlchemy mappers:

* whether a response is streamed rather than serialised, and
* which response fields are *conditionally* populated -- relationship fields
  configured ``lazy="noload"`` serialise as ``[]`` unless the caller passes
  ``include=``, which reads identically to "this thing has no tags".

That second one is the difference between a designer trusting a field and being
misled by it, so it is recorded per field rather than mentioned in prose.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .models import FieldInfo, ParamInfo, ResponseInfo, RouteSurface

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# Response-model suffixes stripped when guessing the ORM class a schema mirrors.
# Longest first so "ReadExtended" wins over "Read".
_SCHEMA_SUFFIXES = (
    "ReadExtended",
    "ReadExpanded",
    "ReadParent",
    "Read",
)


def load_app() -> Any:
    """Import and return the FastAPI application object.

    ``APP_ENV`` is forced to ``test`` when unset so a developer with a
    production ``.env`` on their path cannot accidentally have production
    settings shape the generated document.

    Returns:
        The configured FastAPI instance.

    Raises:
        RuntimeError: If the application cannot be imported or constructed.
    """
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")
    try:
        from app.main import api  # noqa: PLC0415 -- deferred: importing the app

        # constructs it, and that must happen after the APP_ENV default above is
        # in place. A module-level import would run at harness import time.
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            f"Could not import the FastAPI app ({exc}). "
            "Run the harness from the repository root via `uv run`."
        ) from exc
    return api


def _resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve a local ``$ref`` pointer against the document."""
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _render_type(schema: dict[str, Any], spec: dict[str, Any], depth: int = 0) -> str:
    """Render a JSON-schema fragment as a readable Python-ish type."""
    if depth > 6:
        return "..."
    if not schema:
        return "any"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema or "oneOf" in schema:
        parts = [_render_type(s, spec, depth + 1) for s in schema.get("anyOf") or schema["oneOf"]]
        return " | ".join(dict.fromkeys(parts))
    if "allOf" in schema:
        return _render_type(schema["allOf"][0], spec, depth + 1)
    if schema.get("enum") is not None and schema.get("type") is None:
        return "enum"
    type_ = schema.get("type")
    if type_ == "array":
        return f"list[{_render_type(schema.get('items', {}), spec, depth + 1)}]"
    if type_ == "object":
        return "object"
    if type_ == "null":
        return "None"
    mapping = {"integer": "int", "number": "float", "string": "str", "boolean": "bool"}
    base = mapping.get(str(type_), str(type_ or "any"))
    fmt = schema.get("format")
    if base == "str" and fmt in {"date-time", "date", "uuid", "email"}:
        return f"str({fmt})"
    return base


def _is_nullable(schema: dict[str, Any]) -> bool:
    """Whether a schema fragment admits null."""
    if schema.get("type") == "null":
        return True
    for key in ("anyOf", "oneOf"):
        if any(s.get("type") == "null" for s in schema.get(key, [])):
            return True
    return False


_CONSTRAINT_KEYS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "enum",
)


def _constraints(schema: dict[str, Any]) -> dict[str, Any]:
    """Extract declared validation bounds, flattening a nullable union."""
    found: dict[str, Any] = {k: schema[k] for k in _CONSTRAINT_KEYS if k in schema}
    for branch in schema.get("anyOf", []):
        if branch.get("type") != "null":
            found.update({k: branch[k] for k in _CONSTRAINT_KEYS if k in branch})
    return found


def _params(operation: dict[str, Any], spec: dict[str, Any]) -> tuple[ParamInfo, ...]:
    """Build the parameter list for one operation, body included."""
    out: list[ParamInfo] = []
    for param in operation.get("parameters", []):
        schema = param.get("schema", {})
        out.append(
            ParamInfo(
                name=param.get("name", ""),
                location=param.get("in", "query"),
                type_=_render_type(schema, spec),
                required=bool(param.get("required", False)),
                default=schema.get("default"),
                description=param.get("description") or schema.get("description"),
                constraints=_constraints(schema),
            )
        )
    body = operation.get("requestBody")
    if body:
        content = body.get("content", {})
        media = next(iter(content), None)
        schema = content.get(media, {}).get("schema", {}) if media else {}
        out.append(
            ParamInfo(
                name="<body>",
                location="body",
                type_=_render_type(schema, spec),
                required=bool(body.get("required", False)),
                description=body.get("description"),
            )
        )
    return tuple(sorted(out, key=lambda p: (p.location, p.name)))


def _model_fields(
    schema_name: str,
    spec: dict[str, Any],
    conditional: dict[str, dict[str, str]],
) -> tuple[FieldInfo, ...]:
    """List the fields of a named component schema.

    Args:
        schema_name: Component schema name, e.g. ``AssetReadExtended``.
        spec: The full OpenAPI document.
        conditional: Map of schema name -> field name -> the request parameter
            that has to be supplied for the field to be populated.

    Returns:
        The schema's fields. Empty for a schema with no ``properties`` (a
        scalar or a bare container), which the caller renders as such.
    """
    schema = spec.get("components", {}).get("schemas", {}).get(schema_name, {})
    props = schema.get("properties", {})
    if not props:
        return ()
    required = set(schema.get("required", []))
    per_field = conditional.get(schema_name, {})
    fields: list[FieldInfo] = []
    for name, prop in props.items():
        # A generic container (PaginatedResponse[X]) carries its payload under
        # `items`; recurse one level so the report shows the row shape rather
        # than the envelope.
        fields.append(
            FieldInfo(
                name=name,
                type_=_render_type(prop, spec),
                nullable=_is_nullable(prop) or name not in required,
                conditional_on=per_field.get(name),
            )
        )
    return tuple(fields)


def _unwrap_payload_schema(schema_name: str | None, spec: dict[str, Any]) -> str | None:
    """Return the row schema inside a pagination envelope, if there is one.

    ``PaginatedResponse[AssetReadExtended]`` describes the envelope; what a
    designer needs is the shape of one row.
    """
    if not schema_name:
        return None
    schema = spec.get("components", {}).get("schemas", {}).get(schema_name, {})
    items = schema.get("properties", {}).get("items", {})
    if items.get("type") == "array" and "$ref" in items.get("items", {}):
        return items["items"]["$ref"].rsplit("/", 1)[-1]
    return None


def _responses(
    operation: dict[str, Any],
    spec: dict[str, Any],
    conditional: dict[str, dict[str, str]],
) -> tuple[tuple[ResponseInfo, ...], str]:
    """Build the response list and identify the success status."""
    out: list[ResponseInfo] = []
    success = "200"
    for status, body in sorted(operation.get("responses", {}).items()):
        content = body.get("content", {})
        media = next(iter(content), None)
        schema = content.get(media, {}).get("schema", {}) if media else {}
        model = schema.get("$ref", "").rsplit("/", 1)[-1] or None
        if model is None and schema.get("type") == "array":
            inner = schema.get("items", {}).get("$ref", "")
            model = f"list[{inner.rsplit('/', 1)[-1]}]" if inner else None
        fields: tuple[FieldInfo, ...] = ()
        row_model: str | None = None
        if model:
            bare = model.removeprefix("list[").removesuffix("]")
            row_model = _unwrap_payload_schema(bare, spec) or (
                bare if model.startswith("list[") else None
            )
            fields = _model_fields(row_model or bare, spec, conditional)
        out.append(
            ResponseInfo(
                status=status,
                description=body.get("description"),
                model=model,
                fields=fields,
                media_type=media,
                row_model=row_model,
            )
        )
        if status.startswith("2") and status < success:
            success = status
    statuses = [r.status for r in out if r.status.startswith("2")]
    if statuses:
        success = min(statuses)
    return tuple(out), success


def _conditional_fields(spec: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Find response fields that are only populated on request.

    A relationship mapped ``lazy="noload"`` yields an empty collection unless
    the query explicitly eager-loads it, and the list repositories only do that
    when ``include=`` names it. The serialised response is then ``[]``, which is
    indistinguishable from genuinely having none.

    Returns:
        Schema name -> field name -> the parameter that populates it. Empty if
        the ORM cannot be inspected, in which case nothing is claimed.
    """
    try:
        from sqlalchemy import inspect as sa_inspect  # noqa: PLC0415 -- deferred with

        from app import models as app_models  # noqa: PLC0415 -- the app import above.
    except Exception:  # pragma: no cover - defensive
        return {}

    noload: dict[str, set[str]] = {}
    for name in dir(app_models):
        obj = getattr(app_models, name)
        if not (isinstance(obj, type) and name.endswith("ORM")):
            continue
        try:
            mapper = sa_inspect(obj)
            lazy_none = {rel.key for rel in mapper.relationships if rel.lazy == "noload"}
        except Exception:  # pragma: no cover - not a mapped class
            continue
        if lazy_none:
            noload[name.removesuffix("ORM")] = lazy_none

    out: dict[str, dict[str, str]] = {}
    for schema_name in spec.get("components", {}).get("schemas", {}):
        entity = schema_name
        for suffix in _SCHEMA_SUFFIXES:
            if schema_name.endswith(suffix):
                entity = schema_name[: -len(suffix)]
                break
        else:
            continue
        for rel in noload.get(entity, set()):
            props = spec["components"]["schemas"][schema_name].get("properties", {})
            if rel in props:
                out.setdefault(schema_name, {})[rel] = f"include={rel}"
    return out


def _streaming_returns(api: Any) -> set[tuple[str, str]]:
    """Identify routes whose handler returns a streamed response.

    ``QuietClientErrorRoute`` replaces the endpoint callable, but it does so
    through ``functools.wraps``, so the original annotations survive on the
    wrapper and can be read directly.
    """
    from fastapi.routing import APIRoute  # noqa: PLC0415 -- deferred with the app import.

    streaming: set[tuple[str, str]] = set()
    for route in api.routes:
        if not isinstance(route, APIRoute):
            continue
        annotation = getattr(route.endpoint, "__annotations__", {}).get("return")
        name = getattr(annotation, "__name__", str(annotation))
        if "Streaming" in name or "FileResponse" in name:
            for method in route.methods:
                streaming.add((method.upper(), _normalise_path(route.path)))
    return streaming


_PATH_CONVERTER = re.compile(r"\{([^}:]+):[^}]+\}")


def _normalise_path(path: str) -> str:
    """Strip Starlette path converters so a route path matches its OpenAPI path.

    ``/api/assets/{asset_id:int}`` on the router is published as
    ``/api/assets/{asset_id}``. Matching on the raw path silently loses every
    route that uses a converter.
    """
    return _PATH_CONVERTER.sub(r"{\1}", path)


def _handlers(api: Any) -> dict[tuple[str, str], tuple[str, str]]:
    """Map (method, path) to the handler's (module, function name)."""
    from fastapi.routing import APIRoute  # noqa: PLC0415 -- deferred with the app import.

    out: dict[tuple[str, str], tuple[str, str]] = {}
    for route in api.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            out[(method.upper(), _normalise_path(route.path))] = (
                route.endpoint.__module__,
                route.endpoint.__name__,
            )
    return out


def _describe_auth(operation: dict[str, Any], spec: dict[str, Any]) -> str:
    """Describe an operation's authentication requirement.

    The project has no scope model: ``get_current_user`` verifies the token and
    extracts roles, but ``require_roles`` is never applied to a route. Saying
    "bearer, no scope enforced" is materially different from "bearer" for
    someone designing a permissions-aware UI, so it is spelled out.
    """
    security = operation.get("security")
    if not security:
        return "none (unauthenticated)"
    schemes = sorted({name for entry in security for name in entry})
    scopes = sorted({s for entry in security for scopes in entry.values() for s in scopes})
    label = "bearer" if any("bearer" in s.lower() for s in schemes) else ", ".join(schemes)
    if scopes:
        return f"{label}, scopes {', '.join(scopes)}"
    return f"{label}, no scope or role enforced at the route"


def collect(api: Any) -> tuple[tuple[RouteSurface, ...], dict[str, Any]]:
    """Build the static surface for every operation.

    Args:
        api: The FastAPI application object.

    Returns:
        A tuple of (routes sorted by path then method, the OpenAPI document).
    """
    spec = api.openapi()
    conditional = _conditional_fields(spec)
    streaming = _streaming_returns(api)
    handlers = _handlers(api)

    routes: list[RouteSurface] = []
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            upper = method.upper()
            responses, success = _responses(operation, spec, conditional)
            module, func = handlers.get((upper, path), ("UNKNOWN", "UNKNOWN"))
            routes.append(
                RouteSurface(
                    method=upper,
                    path=path,
                    operation_id=operation.get("operationId"),
                    summary=operation.get("summary"),
                    tags=tuple(operation.get("tags", [])),
                    auth=_describe_auth(operation, spec),
                    handler_module=module,
                    handler_name=func,
                    params=_params(operation, spec),
                    request_body=next(
                        (p.type_ for p in _params(operation, spec) if p.location == "body"),
                        None,
                    ),
                    responses=responses,
                    success_status=success,
                    is_streaming=(upper, path) in streaming,
                    trailing_slash_required=path.endswith("/") and path != "/",
                )
            )
    routes.sort(key=lambda r: (r.path, r.method))
    return tuple(routes), spec
