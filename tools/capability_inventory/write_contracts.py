"""Phase 6 -- deriving a write contract from the code and the OpenAPI document.

Everything here is established without contacting anything. It answers 6a
(request contract), the declared half of 6c (error taxonomy), 6e (side effects),
6f (delete semantics), 6g (concurrency) and 6h (auth and audience). What it
cannot answer is what a *database* does when a write violates a constraint --
that needs a real request against a real schema, and lives in
:mod:`.write_probes`.

The centrepiece is :func:`_omission_semantics`, and it is the reason this phase
exists. A management UI's most destructive bug is a partial form that silently
clears the fields it did not send, and nothing in the OpenAPI document says which
way a route behaves.

The case that proved it: ``TitlePatchPublic`` was the request model for both
``PATCH`` and ``PUT /api/titles/{title_id}``, and the two had opposite null
semantics -- the difference being one positional argument at the router's call
into the service, which is why the tracer reads positional arguments and not only
keywords. This phase is what surfaced that, and #181 removed both PUT routes as a
result, so the API no longer contains an example of it. The tracing stays: the
defect was one boolean wide, and nothing but this phase would notice it coming
back.
"""

from __future__ import annotations

import ast
from typing import Any

from .models import (
    ConstraintMapping,
    DeleteSemantics,
    ErrorCase,
    FieldContract,
    RouteAnnotation,
    RouteSurface,
    SideEffect,
    Unknown,
    WriteContract,
)

# Routes that exist for the worker fleet rather than for the front end. A UI
# designed against these is designed against a machine interface: they are a pull
# queue and its heartbeats, and the run-summary sinks the runners post to.
_WORKER_FLEET = frozenset(
    {
        "POST /api/jobs",
        "PATCH /api/jobs/{job_key}/completed",
        "PUT /api/jobs/{job_key}/heartbeat",
        "POST /api/run_summaries",
        "POST /api/runner_state",
        "PATCH /api/runner_state/{runner_key}",
        "POST /api/scanner_run_summaries",
        "POST /api/transform_requests/claim",
        "PATCH /api/transform_requests/{request_id}/heartbeat",
        "POST /api/log",
    }
)

# Values a server assigns. A client that sends one is not obeyed, and a form that
# renders one as an input is lying about what it controls.
_SERVER_CONTROLLED = frozenset({"id", "created_at", "updated_at", "position"})

_CONSTRAINT_KEYS = (
    "maxLength",
    "minLength",
    "maximum",
    "minimum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "pattern",
    "enum",
    "format",
    "multipleOf",
)


def _resolve_ref(node: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Follow a ``$ref`` into the document's component schemas."""
    if not isinstance(node, dict):
        return {}
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return node
    name = ref.rsplit("/", 1)[-1]
    resolved = spec.get("components", {}).get("schemas", {}).get(name, {})
    return resolved if isinstance(resolved, dict) else {}


def _render_type(schema: dict[str, Any]) -> tuple[str, bool]:
    """Render a property's type and say whether it admits an explicit null.

    Returns:
        ``(rendered type, nullable)``.
    """
    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list):
        names: list[str] = []
        nullable = False
        for variant in variants:
            if isinstance(variant, dict) and variant.get("type") == "null":
                nullable = True
                continue
            rendered, _ = _render_type(variant if isinstance(variant, dict) else {})
            names.append(rendered)
        return (" | ".join(n for n in names if n) or "any", nullable)
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1], False
    declared = schema.get("type")
    if declared == "array":
        inner, _ = _render_type(schema.get("items") or {})
        return f"array[{inner}]", False
    if isinstance(declared, str):
        return declared, declared == "null"
    if schema.get("enum"):
        return "enum", False
    return "any", False


def _constraints(schema: dict[str, Any]) -> dict[str, Any]:
    """Collect declared validation bounds, following a nullable union inward."""
    found = {key: schema[key] for key in _CONSTRAINT_KEYS if key in schema}
    for variant in schema.get("anyOf") or schema.get("oneOf") or []:
        if isinstance(variant, dict) and variant.get("type") != "null":
            for key in _CONSTRAINT_KEYS:
                if key in variant and key not in found:
                    found[key] = variant[key]
    return found


def _body_schema(surface: RouteSurface, spec: dict[str, Any]) -> dict[str, Any]:
    """The request body's resolved component schema, or an empty mapping."""
    if not surface.request_body:
        return {}
    return spec.get("components", {}).get("schemas", {}).get(surface.request_body, {}) or {}


def _fields(
    surface: RouteSurface, spec: dict[str, Any], omitted: str, nulled: str | None
) -> tuple[FieldContract, ...]:
    """Build the per-field contract from the request body schema."""
    schema = _body_schema(surface, spec)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    required = set(schema.get("required") or ())
    out: list[FieldContract] = []
    for name in sorted(properties):
        prop = (
            _resolve_ref(properties[name], spec) if "$ref" in properties[name] else properties[name]
        )
        if not isinstance(prop, dict):
            continue
        rendered, nullable = _render_type(prop)
        is_required = name in required
        out.append(
            FieldContract(
                name=name,
                type_=rendered,
                required=is_required,
                nullable=nullable,
                default=prop.get("default"),
                omitted_means="rejected" if is_required else omitted,
                null_means=(nulled if nullable and not is_required else None),
                constraints=_constraints(prop),
                server_controlled=name in _SERVER_CONTROLLED,
            )
        )
    return tuple(out)


def _handler_body(surface: RouteSurface, graph: Any) -> ast.AST | None:
    """The handler's AST, if the code graph parsed it."""
    node = getattr(graph, "functions", {}).get((surface.handler_module, surface.handler_name))
    return node


def _literal(node: ast.AST) -> Any:
    """Evaluate a literal, or return the sentinel ``...`` when it is not one."""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return ...


def _methods_named(graph: Any, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every method with this name, preferring ones that take ``exclude_none``.

    Method names are not unique across services, so a name alone can match the
    wrong class. Ordering by whether the signature even mentions the parameter
    makes the ambiguity harmless: a method that does not take it cannot be the
    one the router is passing it to.
    """
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for _class_name, (_module, class_node) in getattr(graph, "classes", {}).items():
        for item in class_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                found.append(item)
    found.sort(
        key=lambda fn: "exclude_none" not in {a.arg for a in (*fn.args.args, *fn.args.kwonlyargs)}
    )
    return found


def _traced_exclude_none(surface: RouteSurface, graph: Any) -> bool | None:
    """Find the ``exclude_none`` the router hands the service, if it passes one.

    Three call shapes have to be read, because this codebase uses all three:

    - ``update(title_id, update, exclude_none=True)`` -- a keyword literal.
    - ``update(title_id, update, True)`` -- **positional**, which is how the
      Title routes spell it. Reading only keywords misses exactly the pair the
      phase exists to distinguish, so the parameter's index in the callee's
      signature is resolved and the positional argument at that index is read.
    - ``update(title_id, update)`` -- neither, so the callee's own default wins.

    Anything else is left unresolved rather than guessed. A wrong answer here is
    worse than an UNKNOWN, because it would tell a designer a partial form is
    safe when it silently erases every field it omits.
    """
    handler = _handler_body(surface, graph)
    if handler is None:
        return None

    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "exclude_none":
                value = _literal(keyword.value)
                if isinstance(value, bool):
                    return value
        if not isinstance(node.func, ast.Attribute):
            continue
        for method in _methods_named(graph, node.func.attr):
            positional = [a.arg for a in method.args.args if a.arg != "self"]
            if "exclude_none" not in positional:
                continue
            index = positional.index("exclude_none")
            if index < len(node.args):
                value = _literal(node.args[index])
                if isinstance(value, bool):
                    return value
            # Declared but not supplied at this call site: the default applies.
            defaults = list(method.args.defaults)
            if defaults:
                names = positional[len(positional) - len(defaults) :]
                for name, default in zip(names, defaults):
                    if name == "exclude_none":
                        value = _literal(default)
                        if isinstance(value, bool):
                            return value
        for method in _methods_named(graph, node.func.attr):
            for kwarg, kw_default in zip(method.args.kwonlyargs, method.args.kw_defaults):
                if kwarg.arg == "exclude_none" and kw_default is not None:
                    value = _literal(kw_default)
                    if isinstance(value, bool):
                        return value

    # Nothing is passed down at all, because several services do not take the
    # choice as a parameter -- they hard-code it in their own `model_dump` call.
    # `ExternalIdentifierService.update` is the reference case. That is still a
    # derivation from the code, so it belongs here rather than in a declaration.
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        for method in _methods_named(graph, node.func.attr):
            resolved = _model_dump_exclude_none(method)
            if resolved is not None:
                return resolved
    return None


def _model_dump_exclude_none(method: ast.AST) -> bool | None:
    """Read ``model_dump(exclude_none=...)`` out of a service method's own body.

    Returns None when the method makes no such call, or makes more than one that
    disagree -- an inconsistent method is not a contract, and reporting either
    branch as the answer would be a guess.
    """
    seen: set[bool] = set()
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "model_dump":
            continue
        for keyword in node.keywords:
            if keyword.arg == "exclude_none":
                value = _literal(keyword.value)
                if isinstance(value, bool):
                    seen.add(value)
    return seen.pop() if len(seen) == 1 else None


def _all_fields_required(surface: RouteSurface, spec: dict[str, Any]) -> bool:
    """Whether the body has at least one field and every one of them is required."""
    schema = _body_schema(surface, spec)
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return False
    return set(properties) == set(schema.get("required") or ())


def _replaces_a_collection(surface: RouteSurface, spec: dict[str, Any]) -> str | None:
    """The name of a field that replaces a whole collection, if there is one.

    Read from the field's own description rather than from the verb. A route that
    replaces a set has a hazard no per-field analysis finds: sending an empty
    list is a valid, well-formed request that silently removes everything.
    """
    schema = _body_schema(surface, spec)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for name, prop in sorted(properties.items()):
        if not isinstance(prop, dict):
            continue
        description = str(prop.get("description") or "").lower()
        if "replaces the existing" in description:
            return str(name)
    return None


def _omission_semantics(
    surface: RouteSurface, graph: Any, spec: dict[str, Any]
) -> tuple[str, str, str | None]:
    """What omitting a field means, and what sending an explicit null means.

    Returns:
        ``(summary, omitted_means, null_means)``. ``null_means`` is None on a
        create, where there is no prior value for a null to preserve or destroy.
    """
    if surface.request_body is None:
        # Includes every DELETE, and the state-transition routes that carry their
        # whole argument in the path -- retry, reorder, the heartbeats. There is
        # no partial-form hazard where there is no form.
        return ("No request body: the route carries its arguments in the path.", "n/a", None)
    if surface.method == "POST":
        return (
            "Create: an omitted optional field takes its declared default.",
            "the declared default",
            None,
        )

    exclude_none = _traced_exclude_none(surface, graph)
    if exclude_none is True:
        return (
            "Omitted fields are left unchanged, so a partial form is safe. An explicit "
            "`null` is discarded by the same rule, so **a nullable field cannot be "
            "cleared through this route at all** -- there is no request body that sets "
            "one back to null.",
            "unchanged",
            "unchanged -- the field cannot be cleared",
        )
    if exclude_none is False:
        return (
            "Every field of the model is written, whether the caller sent it or not: an "
            "omitted field is applied as `null`. A partial form submitted here **erases "
            "the fields it did not include**. Send a complete object, read from the "
            "server immediately before the write.",
            "set to null",
            "set to null",
        )
    replaced = _replaces_a_collection(surface, spec)
    if replaced is not None:
        return (
            f"Whole-collection replacement: `{replaced}` is required and replaces the "
            f"existing set outright. Omission does not arise -- but sending an empty "
            f"list is a well-formed request that removes everything, so a form must "
            f"never submit `{replaced}` it did not populate from a prior read.",
            "rejected -- the field is required",
            None,
        )
    if _all_fields_required(surface, spec):
        return (
            "Every field of the body is required, so there is no partial-update "
            "hazard: a request either carries the whole object or is rejected with 422.",
            "rejected -- the field is required",
            None,
        )
    return ("UNKNOWN", "UNKNOWN", None)


def _unknown_field_policy(surface: RouteSurface, spec: dict[str, Any]) -> str:
    """Whether an unrecognised field is rejected or silently dropped."""
    schema = _body_schema(surface, spec)
    if not schema:
        return "n/a -- no request body"
    if schema.get("additionalProperties") is False:
        return 'rejected with 422 naming the field (`extra="forbid"`)'
    return "silently ignored -- a misspelled field name is accepted and does nothing"


def _side_effects(annotation: RouteAnnotation | None) -> tuple[SideEffect, ...]:
    """Everything a write changes besides its target row."""
    if annotation is None:
        return ()
    out: list[SideEffect] = []
    for path in annotation.filesystem_access:
        out.append(SideEffect(kind="filesystem", detail=path))
    for work in annotation.background_work:
        out.append(SideEffect(kind="enqueue", detail=work))
    for call in annotation.external_calls:
        out.append(SideEffect(kind="enqueue", detail=f"external call: {call}"))
    return tuple(out)


def _atomicity(annotation: RouteAnnotation | None) -> tuple[bool | None, str]:
    """Whether the operation is all-or-nothing, and what happens when it is not."""
    if annotation is None:
        return None, "UNKNOWN"
    if annotation.filesystem_access:
        return (
            False,
            "Touches the database and the filesystem. The two are not in one "
            "transaction, so a failure in the second half leaves the row committed and "
            "the file untouched, and the response does not distinguish that from "
            "success. A UI must re-read after the write rather than trust the response.",
        )
    if annotation.external_calls:
        return (
            False,
            "Commits a row and then calls an external system. A failure after the "
            "commit leaves the row referring to work that was never started.",
        )
    return True, "Single transaction: the row is written or it is not."


def _concurrency(surface: RouteSurface) -> str:
    """Whether the route offers any optimistic-concurrency mechanism.

    Recorded explicitly rather than left blank. "Everything is last-write-wins" is
    a design constraint a UI has to be built around -- two people editing one
    Title silently overwrite each other, and the interface is the only place that
    can be honest about it.
    """
    headers = {p.name.lower() for p in surface.params if p.location == "header"}
    if {"if-match", "if-unmodified-since"} & headers:
        return "precondition header accepted"
    return (
        "last-write-wins -- no ETag, no `If-Match`, no version column and no "
        "`updated_at` precondition"
    )


def _audience(surface: RouteSurface) -> str:
    """Who a route is for, so a UI is not designed against a machine interface."""
    return "worker fleet" if surface.key in _WORKER_FLEET else "front end"


def _delete_semantics(surface: RouteSurface) -> DeleteSemantics | None:
    """What a DELETE destroys and what it merely detaches.

    The distinction is principle 4 of the design brief: with many-to-many
    membership, "remove from this collection" and "delete permanently" must never
    be the same button. Derived from the path shape, which is reliable here
    because the routes are consistent about it -- a nested route addressing an
    edge detaches, a top-level route addressing an object destroys.
    """
    if surface.method != "DELETE":
        return None
    path = surface.path
    if path.endswith("/tags/{tag_id}"):
        owner = "Title" if "/titles/" in path else "Asset"
        return DeleteSemantics(
            destroys="nothing -- the Tag itself is untouched",
            detaches=f"the edge between this {owner} and the Tag",
            children="none",
            reachable_with_references=True,
            ui_vocabulary=f"Remove tag from this {owner} (never 'Delete tag')",
        )
    if "/contents/" in path:
        return DeleteSemantics(
            destroys="nothing -- the contained Title or Asset survives",
            detaches="one containment edge, and renumbers the surviving siblings",
            children="none",
            reachable_with_references=True,
            ui_vocabulary="Remove from this collection (never 'Delete')",
        )
    if "/ids/" in path:
        return DeleteSemantics(
            destroys="the external identifier record",
            detaches="nothing else",
            children="none",
            reachable_with_references=True,
            ui_vocabulary="Delete identifier",
        )
    if path.endswith("/metadata/{metadata_id}"):
        return DeleteSemantics(
            destroys="the metadata record",
            detaches="nothing else",
            children="none",
            reachable_with_references=True,
            ui_vocabulary="Delete metadata entry",
        )
    if path.endswith("/streams"):
        return DeleteSemantics(
            destroys="every Stream of this Asset",
            detaches="nothing else",
            children="none",
            reachable_with_references=True,
            ui_vocabulary="Delete all streams (plural, and destructive)",
        )
    if path == "/api/artwork/{artwork_id}":
        return DeleteSemantics(
            destroys="the artwork row",
            detaches="nothing else",
            children="none",
            reachable_with_references=None,
            ui_vocabulary="Delete artwork",
        )
    if path == "/api/title_types/{title_type_id}":
        return DeleteSemantics(
            destroys="the Title type",
            detaches="nothing",
            children="blocked",
            reachable_with_references=None,
            ui_vocabulary="Delete type -- blocked while any Title uses it",
        )
    if path == "/api/inbox":
        return DeleteSemantics(
            destroys="a file on disk",
            detaches="nothing",
            children="none",
            reachable_with_references=None,
            ui_vocabulary="Delete file from inbox (destroys bytes, not a row)",
        )
    return DeleteSemantics(
        destroys="UNKNOWN",
        detaches="UNKNOWN",
        children="UNKNOWN",
        reachable_with_references=None,
        ui_vocabulary="UNKNOWN",
    )


def _declared_errors(surface: RouteSurface) -> tuple[ErrorCase, ...]:
    """Non-2xx responses read from the route's own declarations, plus the
    implicit ones FastAPI raises before the handler is ever entered."""
    out: list[ErrorCase] = []
    seen: set[str] = set()
    for response in surface.responses:
        if response.status.startswith("2"):
            continue
        seen.add(response.status)
        out.append(
            ErrorCase(
                status=response.status,
                condition=response.description or "declared by the route",
                body='`{"detail": ...}`',
                usable_message=True,
                source="declared",
            )
        )
    has_body = surface.request_body is not None
    typed_path = any(p.location == "path" for p in surface.params)
    if has_body and "422" not in seen:
        out.append(
            ErrorCase(
                status="422",
                condition="request body fails validation",
                body="FastAPI error list, one entry per field with `loc` and `msg`",
                usable_message=True,
                source="implicit",
                note="raised before the handler runs; per-field and directly renderable",
            )
        )
        seen.add("422")
    if typed_path and "422" not in seen:
        out.append(
            ErrorCase(
                status="422",
                condition="a path parameter is not the declared type",
                body="FastAPI error list",
                usable_message=True,
                source="implicit",
            )
        )
        seen.add("422")
    if not surface.auth.startswith("none") and "401" not in seen:
        out.append(
            ErrorCase(
                status="401",
                condition="bearer token missing or rejected",
                body='`{"detail": ...}`',
                usable_message=True,
                source="implicit",
            )
        )
    return tuple(out)


def _constraint_columns(constraint: Any) -> str:
    """The column names a constraint covers, comma-separated.

    ``Constraint`` does not declare ``columns`` on the base class -- only the
    subclasses that have one do -- so this reads it defensively rather than
    asserting a type the checker cannot narrow here.
    """
    columns = getattr(constraint, "columns", ())
    return ", ".join(str(getattr(c, "name", c)) for c in columns)


def constraints_from_metadata() -> tuple[ConstraintMapping, ...]:
    """Every unique and check constraint the models declare, plus partial unique
    indexes -- which are constraints in everything but name.

    ``uq_parent_asset_once`` is declared as a partial unique ``Index`` rather than
    a ``UniqueConstraint``, because Postgres has no partial unique constraint. It
    guards a duplicate attach exactly as a constraint would, so leaving it out
    because of how it is spelled would omit one of the constraints a UI is most
    likely to hit.

    Returns:
        The inventory, sorted by table then name, with no response information --
        :func:`write_assemble.constraint_map` fills that in from the probes.
    """
    from app.models import Base  # noqa: PLC0415 -- deferred with the app's models.

    out: list[ConstraintMapping] = []
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            name = getattr(constraint, "name", None)
            if not name or not isinstance(name, str) or name.endswith("_pkey"):
                continue
            kind = type(constraint).__name__
            if kind == "UniqueConstraint":
                columns = _constraint_columns(constraint)
                out.append(
                    ConstraintMapping(
                        name=name,
                        table=table.name,
                        kind="unique",
                        definition=f"UNIQUE ({columns})",
                    )
                )
            elif kind == "CheckConstraint":
                out.append(
                    ConstraintMapping(
                        name=name,
                        table=table.name,
                        kind="check",
                        definition=f"CHECK {getattr(constraint, 'sqltext', '')}",
                    )
                )
            elif kind == "ForeignKeyConstraint":
                columns = _constraint_columns(constraint)
                out.append(
                    ConstraintMapping(
                        name=name,
                        table=table.name,
                        kind="foreign key",
                        definition=f"FOREIGN KEY ({columns})",
                    )
                )
        for index in table.indexes:
            if not index.unique or not index.name:
                continue
            where = getattr(index, "dialect_options", {}).get("postgresql", {}).get("where")
            columns = ", ".join(str(getattr(expr, "name", expr)) for expr in index.expressions)
            out.append(
                ConstraintMapping(
                    name=str(index.name),
                    table=table.name,
                    kind="unique (partial index)" if where is not None else "unique (index)",
                    definition=(
                        f"UNIQUE ({columns})" + (f" WHERE {where}" if where is not None else "")
                    ),
                )
            )
    return tuple(sorted(out, key=lambda c: (c.table, c.name)))


def derive(
    surface: RouteSurface,
    annotation: RouteAnnotation | None,
    spec: dict[str, Any],
    graph: Any,
) -> WriteContract:
    """Build the statically-derivable write contract for one endpoint.

    Args:
        surface: The route's Phase 1 surface.
        annotation: The route's Phase 2 annotation, when Phase 2 ran.
        spec: The OpenAPI document, for request-body schemas.
        graph: The parsed application, for the ``exclude_none`` trace.

    Returns:
        A :class:`WriteContract` with ``probed=False``. Fields that only a live
        request can settle -- what a constraint violation returns, whether a
        repeat duplicates -- are left UNKNOWN for :mod:`.write_probes` to fill.
    """
    summary, omitted, nulled = _omission_semantics(surface, graph, spec)
    atomic, atomicity_note = _atomicity(annotation)
    unknowns: list[Unknown] = []
    if summary == "UNKNOWN":
        unknowns.append(
            Unknown(
                scope=surface.key,
                question="whether an omitted field is left unchanged or written as null",
                resolution=(
                    "the router does not pass `exclude_none` and no called service "
                    "method declares a default for it; declare the route in "
                    "write-semantics.yaml, or probe it with --allow-writes"
                ),
            )
        )
    return WriteContract(
        fields=_fields(surface, spec, omitted, nulled),
        unknown_fields=_unknown_field_policy(surface, spec),
        omission_semantics=summary,
        idempotency="UNKNOWN",
        idempotency_evidence=(
            "not probed -- repetition is a property of the database's constraints, not "
            "of the verb, and reading it off the method would be a guess"
        ),
        atomic=atomic,
        atomicity_note=atomicity_note,
        concurrency=_concurrency(surface),
        side_effects=_side_effects(annotation),
        delete=_delete_semantics(surface),
        auth=surface.auth,
        audience=_audience(surface),
        errors=_declared_errors(surface),
        probed=False,
        unknowns=tuple(unknowns),
    )
