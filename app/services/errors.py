# app/services/errors.py
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import ParamSpec, TypeVar, overload

from fastapi import HTTPException

from app.repositories.errors import (
    CheckViolation,
    ConstraintViolation,
    DatabaseLocked,
    EnumViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)

P = ParamSpec("P")
R = TypeVar("R")


def domain_error_detail(
    message: str, error_type: str = "domain_error"
) -> list[dict[str, list[str] | str]]:
    """Build a 422 ``detail`` body matching FastAPI's own validation-error shape.

    FastAPI's built-in request validation errors return
    ``{"detail": [{"loc": [...], "msg": ..., "type": ...}]}``. Domain-level 422s
    raised from the service layer used to return a bare string in ``detail``,
    so API clients had to branch on whether ``detail`` was a string or a list
    depending on which layer produced the error. This keeps the shape
    consistent regardless of origin.

    Args:
        message: Human-readable description of the violation.
        error_type: Short machine-readable error category, mirroring
            Pydantic's ``type`` field in validation errors.

    Returns:
        A single-item list matching FastAPI's validation-error detail shape.
    """
    return [{"loc": [], "msg": message, "type": error_type}]


def conflict_detail(message: str, code: str) -> list[dict[str, list[str] | str]]:
    """Build a 409 ``detail`` body that names *which* conflict occurred.

    Most 409s in this codebase carry a bare human-readable string, which is
    enough when a route has one way to conflict. A move has several -- the edge
    would close a containment cycle, the child already has a home, the
    destination position is taken -- and a drag-and-drop interface has to
    respond differently to each: "you cannot drop a season into its own
    episode" is a refusal to explain, while a taken position is something to
    retry at the next slot. Matching on the prose is not an interface.

    Deliberately the same shape as :func:`domain_error_detail` rather than a
    second one. A client already has to read ``detail[0]["type"]`` for
    domain-level 422s, so reusing it means one parse for both, and ``type``
    stays the field that carries the machine-readable half.

    The existing flat-string 409s are left alone: changing them is a breaking
    change to every current caller, and this route is new so it costs nothing
    to be right from the start.

    Args:
        message: Human-readable description of the conflict.
        code: Short machine-readable discriminator, e.g. ``containment_cycle``.

    Returns:
        A single-item list matching the domain-error detail shape.
    """
    return domain_error_detail(message, code)


@overload
def translate_repository_errors(func: Callable[P, R]) -> Callable[P, R]: ...
@overload
def translate_repository_errors(
    func: None = None, *, not_found_message: str | None = None
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...
def translate_repository_errors(
    func: Callable[P, R] | None = None, *, not_found_message: str | None = None
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Translate repository exceptions into the HTTPException every service raises by hand.

    Maps ``UniqueViolation`` -> 409, ``DatabaseLocked`` -> 423, and the
    remaining constraint-violation types -> 422 (via `domain_error_detail`) --
    the same mapping repeated verbatim across nearly every service method in
    this codebase. Pass `not_found_message` to also translate `NotFoundError`
    -> 404 with that message; omit it to let `NotFoundError` propagate
    unchanged, for callers that need to check existence themselves first (e.g.
    a message that names which of several entities was missing).

    A method that needs a different mapping for one exception type -- e.g.
    `DuplicatePathError`, a `UniqueViolation` subclass with its own 409
    message -- should catch that type explicitly inline rather than use this
    decorator, since it would otherwise be caught here as a plain
    `UniqueViolation` first.

    Usage:
        @translate_repository_errors
        def create_thing(self, thing: ThingCreate) -> ThingRead:
            return self.repo.create(thing)

        @translate_repository_errors(not_found_message="Thing not found")
        def update_thing(self, thing_id: int, update: ThingUpdate) -> ThingRead:
            return self.repo.update(thing_id, update)
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return fn(*args, **kwargs)
            except NotFoundError as e:
                if not_found_message is None:
                    raise
                raise HTTPException(status_code=404, detail=not_found_message) from e
            except UniqueViolation as e:
                raise HTTPException(status_code=409, detail="Unique constraint violated.") from e
            except DatabaseLocked as e:
                raise HTTPException(
                    status_code=423, detail="Database is currently in read-only mode"
                ) from e
            except (
                ForeignKeyViolation,
                NotNullViolation,
                CheckViolation,
                EnumViolation,
                ConstraintViolation,
            ) as e:
                raise HTTPException(status_code=422, detail=domain_error_detail(str(e))) from e

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
