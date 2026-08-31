"""Rebuild an :class:`~.models.Inventory` from a previous run's JSON.

The JSON artefact is the complete record of a run, which makes it possible to
re-render the Markdown without touching the database or the API again. That
matters for two reasons: a change to the report's presentation should produce a
diff of presentation alone, with no timing noise to read past; and iterating on
the renderer should not require a seven-minute probe suite each time.

Reconstruction is written out field by field rather than driven generically off
the dataclass signatures. It is more code, but a schema drift then fails here
with the field that moved, instead of surfacing as a mysteriously empty section
three hundred lines into a rendered document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    CollectionStats,
    ColumnStats,
    DataShape,
    EndpointRecord,
    FieldInfo,
    FilterCoverage,
    IndexInfo,
    Inventory,
    PaginationInfo,
    ParamInfo,
    ProbeResult,
    QueryInfo,
    ResponseInfo,
    RouteAnnotation,
    RouteSurface,
    Timing,
    Unknown,
    UsageEvidence,
)


class InventoryFormatError(RuntimeError):
    """Raised when a JSON artefact does not match the expected schema."""


def _require(payload: dict[str, Any], key: str, context: str) -> Any:
    """Fetch a required key, naming the context when it is absent.

    Raises:
        InventoryFormatError: If the key is missing.
    """
    if key not in payload:
        raise InventoryFormatError(
            f"{context} is missing the {key!r} field. The JSON was probably written "
            "by a different version of the harness; re-run it rather than "
            "re-rendering."
        )
    return payload[key]


def _unknown(payload: dict[str, Any]) -> Unknown:
    return Unknown(
        scope=payload["scope"],
        question=payload["question"],
        resolution=payload["resolution"],
    )


def _param(payload: dict[str, Any]) -> ParamInfo:
    return ParamInfo(
        name=payload["name"],
        location=payload["location"],
        type_=payload["type_"],
        required=payload["required"],
        default=payload.get("default"),
        description=payload.get("description"),
        constraints=dict(payload.get("constraints") or {}),
    )


def _field(payload: dict[str, Any]) -> FieldInfo:
    return FieldInfo(
        name=payload["name"],
        type_=payload["type_"],
        nullable=payload["nullable"],
        conditional_on=payload.get("conditional_on"),
    )


def _response(payload: dict[str, Any]) -> ResponseInfo:
    return ResponseInfo(
        status=payload["status"],
        description=payload.get("description"),
        model=payload.get("model"),
        fields=tuple(_field(f) for f in payload.get("fields") or ()),
        media_type=payload.get("media_type"),
        row_model=payload.get("row_model"),
    )


def _surface(payload: dict[str, Any]) -> RouteSurface:
    return RouteSurface(
        method=_require(payload, "method", "a route surface"),
        path=_require(payload, "path", "a route surface"),
        operation_id=payload.get("operation_id"),
        summary=payload.get("summary"),
        tags=tuple(payload.get("tags") or ()),
        auth=payload["auth"],
        handler_module=payload["handler_module"],
        handler_name=payload["handler_name"],
        params=tuple(_param(p) for p in payload.get("params") or ()),
        request_body=payload.get("request_body"),
        responses=tuple(_response(r) for r in payload.get("responses") or ()),
        success_status=payload["success_status"],
        is_streaming=payload["is_streaming"],
        trailing_slash_required=payload["trailing_slash_required"],
    )


def _query(payload: dict[str, Any]) -> QueryInfo:
    return QueryInfo(
        owner=payload["owner"],
        kind=payload["kind"],
        tables=tuple(payload.get("tables") or ()),
        in_loop=payload["in_loop"],
        loop_note=payload.get("loop_note"),
        writes=payload["writes"],
        line=payload["line"],
        source_file=payload["source_file"],
    )


def _coverage(payload: dict[str, Any]) -> FilterCoverage:
    return FilterCoverage(
        param=payload["param"],
        role=payload["role"],
        table=payload.get("table"),
        column=payload.get("column"),
        operator=payload.get("operator"),
        covered=payload.get("covered"),
        index=payload.get("index"),
        note=payload["note"],
    )


def _pagination(payload: dict[str, Any]) -> PaginationInfo:
    return PaginationInfo(
        style=payload["style"],
        default_limit=payload.get("default_limit"),
        max_limit=payload.get("max_limit"),
        sort_fields=tuple(payload.get("sort_fields") or ()),
        default_sort=payload.get("default_sort"),
        stable_under_writes=payload.get("stable_under_writes"),
        stability_note=payload.get("stability_note", ""),
        deep_page_ceiling=payload.get("deep_page_ceiling"),
    )


def _annotation(payload: dict[str, Any] | None) -> RouteAnnotation | None:
    if payload is None:
        return None
    return RouteAnnotation(
        service=payload.get("service"),
        repositories=tuple(payload.get("repositories") or ()),
        queries=tuple(_query(q) for q in payload.get("queries") or ()),
        n_plus_one=tuple(_query(q) for q in payload.get("n_plus_one") or ()),
        coverage=tuple(_coverage(c) for c in payload.get("coverage") or ()),
        pagination=_pagination(_require(payload, "pagination", "a route annotation")),
        external_calls=tuple(payload.get("external_calls") or ()),
        background_work=tuple(payload.get("background_work") or ()),
        hard_limits=tuple(payload.get("hard_limits") or ()),
        filesystem_access=tuple(payload.get("filesystem_access") or ()),
        unknowns=tuple(_unknown(u) for u in payload.get("unknowns") or ()),
    )


def _timing(payload: dict[str, Any] | None) -> Timing | None:
    if payload is None:
        return None
    return Timing(
        runs=payload["runs"],
        p50_ms=payload["p50_ms"],
        p95_ms=payload["p95_ms"],
        min_ms=payload["min_ms"],
        max_ms=payload["max_ms"],
        ttfb_p50_ms=payload.get("ttfb_p50_ms"),
        ttfb_p95_ms=payload.get("ttfb_p95_ms"),
    )


def _probe(payload: dict[str, Any]) -> ProbeResult:
    return ProbeResult(
        name=payload["name"],
        endpoint_key=payload["endpoint_key"],
        method=payload["method"],
        url=payload["url"],
        status=payload["status"],
        http_status=payload.get("http_status"),
        timing=_timing(payload.get("timing")),
        bytes_=payload.get("bytes_"),
        item_count=payload.get("item_count"),
        reason=payload.get("reason"),
        notes=tuple(payload.get("notes") or ()),
    )


def _usage(payload: dict[str, Any] | None) -> UsageEvidence | None:
    if payload is None:
        return None
    return UsageEvidence(
        endpoint_key=payload["endpoint_key"],
        referenced=payload["referenced"],
        strength=payload["strength"],
        callers=tuple(payload.get("callers") or ()),
        test_references=tuple(payload.get("test_references") or ()),
        note=payload["note"],
    )


def _endpoint(payload: dict[str, Any]) -> EndpointRecord:
    return EndpointRecord(
        surface=_surface(_require(payload, "surface", "an endpoint record")),
        annotation=_annotation(payload.get("annotation")),
        probes=tuple(_probe(p) for p in payload.get("probes") or ()),
        usage=_usage(payload.get("usage")),
        risks=tuple(payload.get("risks") or ()),
        verdict=payload.get("verdict", "UNKNOWN"),
        verdict_class=payload.get("verdict_class", "unknown"),
    )


def _index(payload: dict[str, Any]) -> IndexInfo:
    return IndexInfo(
        name=payload["name"],
        table=payload["table"],
        columns=tuple(payload.get("columns") or ()),
        unique=payload["unique"],
        expression=payload.get("expression"),
        where=payload.get("where"),
        # Defaulted, not required: a JSON written before the field existed still
        # loads. Dropping it instead would silently turn every GIN index back into
        # a btree, and `covering()` would offer one for an ordering it cannot serve.
        method=payload.get("method", "btree"),
        source=payload.get("source", "models"),
    )


def _column_stats(payload: dict[str, Any]) -> ColumnStats:
    return ColumnStats(
        table=payload["table"],
        column=payload["column"],
        fill_rate=payload["fill_rate"],
        non_null=payload["non_null"],
        total=payload["total"],
        distinct=payload.get("distinct"),
        distinct_capped=payload.get("distinct_capped", False),
        facet_candidate=payload.get("facet_candidate", False),
        top_values=tuple(tuple(pair) for pair in payload.get("top_values") or ()),  # type: ignore[misc]
    )


def _collection_stats(payload: dict[str, Any]) -> CollectionStats:
    return CollectionStats(
        parent_table=payload["parent_table"],
        child_table=payload["child_table"],
        fk_column=payload["fk_column"],
        parents_with_children=payload["parents_with_children"],
        parents_total=payload["parents_total"],
        min_children=payload["min_children"],
        median_children=payload["median_children"],
        p95_children=payload["p95_children"],
        max_children=payload["max_children"],
    )


def _data_shape(payload: dict[str, Any] | None) -> DataShape | None:
    if payload is None:
        return None
    return DataShape(
        row_counts=dict(payload.get("row_counts") or {}),
        columns=tuple(_column_stats(c) for c in payload.get("columns") or ()),
        collections=tuple(_collection_stats(c) for c in payload.get("collections") or ()),
        server_version=payload["server_version"],
        captured_from=payload["captured_from"],
        unknowns=tuple(_unknown(u) for u in payload.get("unknowns") or ()),
        baseline_rtt_ms=payload.get("baseline_rtt_ms"),
    )


def from_json(path: Path) -> Inventory:
    """Rebuild an inventory from a JSON artefact written by a previous run.

    Args:
        path: Path to ``docs/capability-inventory.json`` or an equivalent.

    Returns:
        The reconstructed inventory, ready to hand to :mod:`.render`.

    Raises:
        InventoryFormatError: If the file is missing, is not JSON, or does not
            carry the fields this version of the harness expects.
    """
    if not path.is_file():
        raise InventoryFormatError(f"No inventory JSON at {path}.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InventoryFormatError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InventoryFormatError(f"{path} does not contain an inventory object.")

    try:
        return Inventory(
            generated_from=_require(payload, "generated_from", "the inventory"),
            app_version=payload.get("app_version", "unknown"),
            phases_run=tuple(payload.get("phases_run") or ()),
            phases_skipped=tuple(payload.get("phases_skipped") or ()),
            endpoints=tuple(_endpoint(e) for e in payload.get("endpoints") or ()),
            indexes=tuple(_index(i) for i in payload.get("indexes") or ()),
            data_shape=_data_shape(payload.get("data_shape")),
            unknowns=tuple(_unknown(u) for u in payload.get("unknowns") or ()),
            notes=tuple(payload.get("notes") or ()),
        )
    except KeyError as exc:
        raise InventoryFormatError(
            f"{path} is missing the {exc} field. It was probably written by a "
            "different version of the harness; re-run it rather than re-rendering."
        ) from exc
