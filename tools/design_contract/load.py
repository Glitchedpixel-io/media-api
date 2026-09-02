"""Typed access to the capability inventory JSON.

The inventory is a large, uniform document. This module exposes only the parts
the contract needs, so a change in the inventory's shape fails here rather than
halfway through rendering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Timing:
    """A measured probe result.

    Attributes:
        probe: Name of the probe scenario from ``probes.yaml``.
        p50_ms: Median observed latency in milliseconds.
        p95_ms: 95th-percentile observed latency in milliseconds.
        runs: Number of runs behind the percentiles.
        item_count: Rows returned by the probe, if it returned a collection.
    """

    probe: str
    p50_ms: float | None
    p95_ms: float | None
    runs: int
    item_count: int | None


@dataclass(frozen=True)
class Field:
    """One field of a response body.

    Attributes:
        name: Field name as it appears in JSON.
        type_: Rendered type, e.g. ``str | None``.
        nullable: Whether the field may be null.
        conditional_on: Query parameter that must be sent for the field to be
            populated, e.g. ``include=tags``. ``None`` for unconditional fields.
    """

    name: str
    type_: str
    nullable: bool
    conditional_on: str | None


@dataclass(frozen=True)
class Param:
    """One request parameter.

    Attributes:
        name: Parameter name.
        type_: Rendered type.
        location: ``query``, ``path`` or ``header``.
        required: Whether the request fails without it.
        default: Default applied when omitted.
        constraints: Validation bounds, e.g. ``{"maximum": 500}``.
    """

    name: str
    type_: str
    location: str
    required: bool
    default: Any
    constraints: dict[str, Any]


class Endpoint:
    """One endpoint of the API, as the contract needs to see it."""

    def __init__(self, raw: dict[str, Any]) -> None:
        """Wrap a raw inventory endpoint record.

        Args:
            raw: One element of the inventory's ``endpoints`` list.
        """
        self._raw = raw
        self._surface: dict[str, Any] = raw["surface"]
        self._annotation: dict[str, Any] = raw["annotation"]

    @property
    def operation_id(self) -> str:
        """Stable identifier used to key ``surfaces.yaml``."""
        return str(self._surface["operation_id"])

    @property
    def method(self) -> str:
        """HTTP method, upper case."""
        return str(self._surface["method"])

    @property
    def path(self) -> str:
        """URL path, with ``{placeholders}``."""
        return str(self._surface["path"])

    @property
    def route(self) -> str:
        """``METHOD /path``, for display."""
        return f"{self.method} {self.path}"

    @property
    def is_write(self) -> bool:
        """Whether this endpoint changes state."""
        return self.method in {"POST", "PATCH", "PUT", "DELETE"}

    @property
    def params(self) -> list[Param]:
        """Request parameters, in declaration order."""
        return [
            Param(
                name=p["name"],
                type_=p["type_"],
                location=p["location"],
                required=bool(p["required"]),
                default=p["default"],
                constraints=p["constraints"] or {},
            )
            for p in self._surface["params"]
        ]

    def _success_response(self) -> dict[str, Any] | None:
        """Return the 2xx response record, or ``None`` if there is not one."""
        for response in self._surface["responses"] or []:
            if str(response["status"]).startswith("2"):
                return dict(response)
        return None

    @property
    def row_model(self) -> str | None:
        """Schema name of a single returned row, if the response is a list."""
        response = self._success_response()
        if response is None:
            return None
        return response.get("row_model")

    @property
    def response_model(self) -> str | None:
        """Schema name of the whole success response body."""
        response = self._success_response()
        if response is None:
            return None
        return response.get("model")

    @property
    def fields(self) -> list[Field]:
        """Fields of the success response body."""
        response = self._success_response()
        if response is None:
            return []
        return [
            Field(
                name=f["name"],
                type_=f["type_"],
                nullable=bool(f["nullable"]),
                conditional_on=f["conditional_on"],
            )
            for f in response.get("fields") or []
        ]

    @property
    def pagination(self) -> dict[str, Any]:
        """Pagination annotation: style, default and max limit, sort fields."""
        return self._annotation.get("pagination") or {}

    @property
    def coverage(self) -> list[dict[str, Any]]:
        """Per-parameter index coverage records."""
        return self._annotation.get("coverage") or []

    @property
    def write_contract(self) -> dict[str, Any] | None:
        """Write contract record, or ``None`` for read endpoints."""
        return self._raw.get("write_contract")

    @property
    def audience(self) -> str | None:
        """Declared audience of a write endpoint, e.g. ``worker fleet``."""
        contract = self.write_contract
        return contract["audience"] if contract else None

    @property
    def request_body_fields(self) -> list[dict[str, Any]]:
        """Fields accepted in the request body."""
        contract = self.write_contract
        if not contract:
            return []
        return contract.get("fields") or []

    @property
    def timings(self) -> list[Timing]:
        """Measured probe timings, slowest first."""
        results: list[Timing] = []
        for probe in self._raw.get("probes") or []:
            if probe.get("status") != "ok":
                continue
            timing = probe.get("timing") or {}
            if timing.get("p50_ms") is None:
                continue
            results.append(
                Timing(
                    probe=probe["name"],
                    p50_ms=timing.get("p50_ms"),
                    p95_ms=timing.get("p95_ms"),
                    runs=int(timing.get("runs") or 0),
                    item_count=probe.get("item_count"),
                )
            )
        return sorted(results, key=lambda t: t.p95_ms or 0.0, reverse=True)


class Inventory:
    """The capability inventory, indexed for the contract generator."""

    def __init__(self, raw: dict[str, Any]) -> None:
        """Index a parsed inventory document.

        Args:
            raw: The whole parsed ``capability-inventory.json``.
        """
        self._raw = raw
        self.endpoints: dict[str, Endpoint] = {}
        for record in raw["endpoints"]:
            endpoint = Endpoint(record)
            self.endpoints[endpoint.operation_id] = endpoint
        self._fill: dict[tuple[str, str], float] = {
            (c["table"], c["column"]): c["fill_rate"]
            for c in raw["data_shape"]["columns"]
            if c.get("fill_rate") is not None
        }
        self._collections: dict[tuple[str, str], dict[str, Any]] = {}
        for record in raw["data_shape"]["collections"]:
            key = (record["parent_table"], record["child_table"])
            existing = self._collections.get(key)
            # Two foreign keys can join the same pair of tables. Keep the wider
            # one: it is the worst case a screen has to render.
            if existing is None or (record["max_children"] or 0) > (existing["max_children"] or 0):
                self._collections[key] = record

    @classmethod
    def from_path(cls, path: Path) -> Inventory:
        """Load an inventory from a JSON file.

        Args:
            path: Path to ``capability-inventory.json``.

        Returns:
            The indexed inventory.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @property
    def app_version(self) -> str:
        """Version of media-api the inventory was generated from."""
        return str(self._raw.get("app_version", "unknown"))

    @property
    def row_counts(self) -> dict[str, int]:
        """Row count per table at capture time."""
        return dict(self._raw["data_shape"]["row_counts"])

    def fill_rate(self, table: str | None, column: str) -> float | None:
        """Return the fill rate for a column, or ``None`` if not measured.

        Args:
            table: Table name, or ``None`` when the model maps to no table.
            column: Column name, assumed to match the response field name.

        Returns:
            Share of rows where the column is non-null, or ``None`` when the
            pairing was not measured. ``None`` is deliberate: a guessed fill
            rate is worse than an absent one.
        """
        if table is None:
            return None
        return self._fill.get((table, column))

    def fan_out(self, parent: str | None, child: str | None) -> dict[str, Any] | None:
        """Return how many child rows one parent row has.

        This is the number that matters for a collection scoped to one parent.
        The table's total row count is not: an Asset's stream list is bounded by
        the streams *per asset*, not by every stream in the library.

        Args:
            parent: Parent table name.
            child: Child table name.

        Returns:
            The collection record with ``median_children``, ``p95_children`` and
            ``max_children``, or ``None`` when the pairing was not measured.
        """
        if parent is None or child is None:
            return None
        return self._collections.get((parent, child))
