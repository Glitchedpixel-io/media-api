# app/repositories/_exc.py
from __future__ import annotations

from sqlalchemy.exc import DataError, IntegrityError

from .errors import (
    CheckViolation,
    ConstraintViolation,
    DuplicatePathError,
    EnumViolation,
    ForeignKeyViolation,
    NotNullViolation,
    UniqueViolation,
)


def _extract_constraint_name(err: Exception) -> str | None:
    # SQLAlchemy will often carry .orig (psycopg2 / sqlite3 exception)
    # Constraint name extraction is driver-specific; we fall back to message scan.
    msg = str(err)
    # Common patterns you might see; add more as needed.
    for key in [
        "uq_assets_path",  # PG named unique constraint
        "UNIQUE constraint failed",  # SQLite message
        "unique constraint",  # generic
        "fk_",
        "ix_",
        "ck_",
        "pk_",  # prefixes you use
    ]:
        if key in msg:
            return key
    return None


def _is_unique_message(msg: str) -> bool:
    msg_lower = msg.lower()
    return (
        "unique constraint" in msg_lower
        or "duplicate key value" in msg_lower
        or "is not unique" in msg_lower
        or "UNIQUE constraint failed" in msg
    )


def _is_fk_message(msg: str) -> bool:
    ml = msg.lower()
    return "foreign key constraint" in ml or "violates foreign key constraint" in ml


def _is_notnull_message(msg: str) -> bool:
    ml = msg.lower()
    return (
        "not null constraint" in ml
        or "null value in column" in ml
        or "NOT NULL constraint failed" in msg
    )


def _is_check_message(msg: str) -> bool:
    ml = msg.lower()
    return "check constraint" in ml


def _is_enum_message(msg: str) -> bool:
    # PG enum errors usually mention "is not a valid enum"
    ml = msg.lower()
    return "is not a valid enum" in ml or "invalid input value for enum" in ml


def map_sqla_error(exc: Exception) -> ConstraintViolation:
    """
    Map SQLAlchemy/driver exceptions into domain exceptions.
    Falls back to generic ConstraintViolation if we cannot classify.
    """
    msg = str(getattr(exc, "orig", exc))
    constraint = _extract_constraint_name(getattr(exc, "orig", exc))

    if isinstance(exc, IntegrityError):
        if _is_unique_message(msg):
            # Specialize for your hot-path unique constraint on assets.path
            if (
                constraint == "uq_assets_path"
                or "assets_path_key" in msg
                or "UNIQUE constraint failed: assets.path" in msg
            ):
                return DuplicatePathError(
                    "Asset path must be unique.", constraint=constraint, column="path"
                )
            return UniqueViolation("Unique constraint violated.", constraint=constraint)
        if _is_fk_message(msg):
            return ForeignKeyViolation("Foreign key constraint violated.", constraint=constraint)
        if _is_notnull_message(msg):
            return NotNullViolation("NOT NULL constraint violated.", constraint=constraint)
        if _is_check_message(msg):
            return CheckViolation("CHECK constraint violated.", constraint=constraint)

    if isinstance(exc, DataError) or _is_enum_message(msg):
        return EnumViolation("Invalid enum value.", constraint=constraint)

    # Fallback: generic classification
    return ConstraintViolation("Constraint violated.", constraint=constraint)
