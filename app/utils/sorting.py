# app/utils/sorting.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, asc, desc, func, inspect

# Useful sentinels (pick what matches your semantics)
DT_MAX = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
DT_MIN = datetime(1, 1, 1, 0, 0, 0, tzinfo=UTC)


Direction = str


@dataclass(frozen=True)
class SortConfig:
    model: type
    allowed_fields: set[str]
    id_field: str = "id"
    # Sentinels only for nullable fields you may sort on:
    # e.g. {"created_at": {"asc": datetime_max_utc, "desc": datetime_min_utc}, "name": {"asc": "\uffff", "desc": ""}}
    sentinels: dict[str, dict[Direction, Any]] = field(default_factory=dict)
    # Optional: map a sort field name to a custom SQLAlchemy expression for ORDER BY
    # e.g. {"name_ci": func.lower(model.name)}
    field_overrides: dict[str, ColumnElement] = field(default_factory=dict)


def normalize_sort(sort_spec: str, config: SortConfig) -> list[tuple[str, Direction]]:
    from app.repositories.errors import EnumViolation

    out: list[tuple[str, Direction]] = []
    seen: set[str] = set()
    for raw in (p.strip() for p in sort_spec.split(",") if p.strip()):
        field, _, direction = raw.partition(":")
        field = field.strip()
        direction = (direction or "asc").strip().lower()
        if field not in config.allowed_fields:
            raise EnumViolation(f"Unsupported sort field: {field!r}")
        if direction not in {"asc", "desc"}:
            raise EnumViolation(f"Invalid sort direction for {field!r}: {direction!r}")
        if field in seen:
            continue
        out.append((field, direction))
        seen.add(field)

    # Ensure deterministic tie-breaker: id last (preserve requested direction if present)
    if config.id_field in seen:
        # move id to the end
        id_pair = next(t for t in out if t[0] == config.id_field)
        out = [(f, d) for (f, d) in out if f != config.id_field] + [id_pair]
    else:
        out.append((config.id_field, "asc"))
    return out


def _col_for_field(config: SortConfig, field: str) -> ColumnElement:
    # Custom expression override?
    if field in config.field_overrides:
        return config.field_overrides[field]
    # Regular model column
    try:
        return getattr(config.model, field)  # type: ignore
    except AttributeError:
        from app.repositories.errors import EnumViolation

        raise EnumViolation(f"Model {config.model.__name__} has no column {field!r}")


def _maybe_coalesce(
    config: SortConfig, field: str, col: ColumnElement, direction: Direction
) -> ColumnElement:
    # Apply COALESCE only if: (1) field has a sentinel, and (2) column is nullable
    # (sqlakeyset requires NON-NULL keyset values)
    has_sentinel = field in config.sentinels and direction in config.sentinels[field]
    if not has_sentinel:
        return col
    try:
        is_nullable: bool = inspect(config.model).columns[field].nullable
    except Exception:
        # If we can't inspect nullability (override/expression), assume nullable and coalesce.
        is_nullable = True
    return func.coalesce(col, config.sentinels[field][direction]) if is_nullable else col


def build_order_by(config: SortConfig, sort_spec: str) -> list[ColumnElement]:
    clauses: list[ColumnElement] = []
    for field, direction in normalize_sort(sort_spec, config):
        col = _col_for_field(config, field)
        col = _maybe_coalesce(config, field, col, direction)
        clauses.append(asc(col) if direction == "asc" else desc(col))
    return clauses


def apply_ordering(stmt: Select, config: SortConfig, sort_spec: str) -> Select:
    return stmt.order_by(*build_order_by(config, sort_spec))
