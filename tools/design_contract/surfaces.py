"""Load and validate the hand-maintained surface map.

Validation is the point of this module. ``surfaces.yaml`` is edited by hand, so
a typo would otherwise drop an endpoint from the contract silently. Every
operation must be accounted for exactly once, every name must resolve, and every
availability claim must still agree with the inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .load import Inventory


class SurfaceMapError(Exception):
    """Raised when ``surfaces.yaml`` disagrees with the inventory."""


@dataclass(frozen=True)
class Surface:
    """One design surface and the endpoints it uses.

    Attributes:
        key: Short identifier, e.g. ``library``.
        title: Display name from the brief, e.g. ``Library``.
        summary: One-paragraph description of what the surface is for.
        primary: Operations rendered in full.
        also: Operations rendered as one line each.
    """

    key: str
    title: str
    summary: str
    primary: list[str] = field(default_factory=list)
    also: list[str] = field(default_factory=list)

    @property
    def operations(self) -> list[str]:
        """All operations on this surface, primary first."""
        return [*self.primary, *self.also]


@dataclass(frozen=True)
class Note:
    """A free-text entry keyed to an operation, e.g. a do-not-call reason."""

    operation: str
    reason: str


@dataclass(frozen=True)
class Gap:
    """A capability the API cannot serve.

    Attributes:
        capability: What the front-end wants.
        issue: Issue reference, or ``none raised``.
        detail: What a designer must do instead.
    """

    capability: str
    issue: str
    detail: str


@dataclass(frozen=True)
class Resolved:
    """A brief-stated gap the API has since closed.

    Attributes:
        brief: The claim as the brief states it.
        now: What serves the capability today.
    """

    brief: str
    now: str


@dataclass(frozen=True)
class SurfaceMap:
    """The whole validated surface map."""

    surfaces: list[Surface]
    models: dict[str, str | None]
    path_roots: dict[str, str]
    fan_out: dict[str, list[str]]
    do_not_call: list[Note]
    unassigned: list[Note]
    not_available: list[Gap]
    resolved: list[Resolved]

    def primary_owner(self, operation: str) -> str | None:
        """Return the surface key that renders an operation in full.

        Args:
            operation: Operation id to look up.

        Returns:
            Key of the first surface listing it as primary, or ``None`` if no
            surface does.
        """
        for surface in self.surfaces:
            if operation in surface.primary:
                return surface.key
        return None


def _assert_reference(inventory: Inventory, spec: dict[str, Any], errors: list[str]) -> None:
    """Check one ``present_*``/``absent_*`` assertion against the inventory.

    These assertions are what stop the document from carrying a stale claim.
    A gap that has been closed, or a capability that has regressed, fails the
    build rather than shipping into a designer's permanent context.

    Args:
        inventory: The loaded inventory.
        spec: A ``not_available`` or ``resolved_since_brief`` entry.
        errors: Accumulator appended to on failure.
    """
    label = spec.get("capability") or spec.get("brief") or "<entry>"

    def has_op(name: str) -> bool:
        return name in inventory.endpoints

    def has_param(ref: str) -> bool:
        op, _, param = ref.partition(".")
        endpoint = inventory.endpoints.get(op)
        return endpoint is not None and any(p.name == param for p in endpoint.params)

    def has_field(ref: str) -> bool:
        op, _, name = ref.partition(".")
        endpoint = inventory.endpoints.get(op)
        return endpoint is not None and any(f.name == name for f in endpoint.fields)

    checks: list[tuple[str, bool, bool]] = []
    for key, probe in (
        ("present_op", has_op),
        ("present_param", has_param),
        ("present_field", has_field),
    ):
        if spec.get(key):
            checks.append((f"{key}={spec[key]}", probe(str(spec[key])), True))
    for key, probe in (
        ("absent_op", has_op),
        ("absent_param", has_param),
        ("absent_field", has_field),
    ):
        if spec.get(key):
            checks.append((f"{key}={spec[key]}", probe(str(spec[key])), False))

    for description, found, want_present in checks:
        if found != want_present:
            state = "missing" if want_present else "present"
            errors.append(
                f"{label!r}: {description} is {state} in the inventory. "
                "The claim in surfaces.yaml is out of date."
            )


def load_surface_map(path: Path, inventory: Inventory) -> SurfaceMap:
    """Load ``surfaces.yaml`` and validate it against the inventory.

    Args:
        path: Path to the surface map.
        inventory: The loaded capability inventory.

    Returns:
        The validated surface map.

    Raises:
        SurfaceMapError: If any operation is unknown, assigned more than once,
            or left unaccounted for, or if an availability claim no longer
            agrees with the inventory.
    """
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    errors: list[str] = []

    surfaces = [
        Surface(
            key=key,
            title=body["title"],
            summary=" ".join(str(body["summary"]).split()),
            primary=list(body.get("primary") or []),
            also=list(body.get("also") or []),
        )
        for key, body in (raw.get("surfaces") or {}).items()
    ]
    # A null reason means the entry is covered by another line's reason; it is
    # still listed so the "every operation is accounted for" check stays honest.
    do_not_call = [
        Note(operation=n["operation"], reason=" ".join(str(n["reason"] or "").split()))
        for n in raw.get("do_not_call") or []
    ]
    unassigned = [
        Note(operation=n["operation"], reason=" ".join(str(n["reason"] or "").split()))
        for n in raw.get("unassigned") or []
    ]

    # Every name must resolve to a real operation.
    known = set(inventory.endpoints)
    assigned: dict[str, list[str]] = {}
    for surface in surfaces:
        for operation in surface.operations:
            assigned.setdefault(operation, []).append(surface.key)
    for note_list, bucket in ((do_not_call, "do_not_call"), (unassigned, "unassigned")):
        for note in note_list:
            assigned.setdefault(note.operation, []).append(bucket)

    for operation, owners in sorted(assigned.items()):
        if operation not in known:
            errors.append(
                f"{operation!r} (in {', '.join(owners)}) is not an operation_id "
                "in the inventory."
            )

    # An operation may sit on several surfaces, but never on a surface and in
    # do_not_call, and never in both terminal buckets.
    for operation, owners in sorted(assigned.items()):
        terminal = [o for o in owners if o in {"do_not_call", "unassigned"}]
        if terminal and len(owners) > 1:
            errors.append(
                f"{operation!r} is in {', '.join(terminal)} and also "
                f"{', '.join(o for o in owners if o not in terminal) or 'itself'}. "
                "It must be one or the other."
            )

    # Nothing may be forgotten.
    for operation in sorted(known - set(assigned)):
        endpoint = inventory.endpoints[operation]
        errors.append(
            f"{operation!r} ({endpoint.route}) is in the inventory but is not "
            "assigned to a surface, do_not_call or unassigned."
        )

    not_available: list[Gap] = []
    for entry in raw.get("not_available") or []:
        _assert_reference(inventory, entry, errors)
        not_available.append(
            Gap(
                capability=entry["capability"],
                issue=str(entry.get("issue", "none raised")),
                detail=" ".join(str(entry["detail"]).split()),
            )
        )

    resolved: list[Resolved] = []
    for entry in raw.get("resolved_since_brief") or []:
        _assert_reference(inventory, entry, errors)
        resolved.append(Resolved(brief=entry["brief"], now=" ".join(str(entry["now"]).split())))

    if errors:
        raise SurfaceMapError(
            "surfaces.yaml does not agree with the inventory:\n  - " + "\n  - ".join(errors)
        )

    return SurfaceMap(
        surfaces=surfaces,
        models=dict(raw.get("models") or {}),
        path_roots=dict(raw.get("path_roots") or {}),
        fan_out={k: list(v) for k, v in (raw.get("fan_out") or {}).items()},
        do_not_call=do_not_call,
        unassigned=unassigned,
        not_available=not_available,
        resolved=resolved,
    )
