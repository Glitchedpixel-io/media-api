# app/repositories/errors.py
from __future__ import annotations


class RepositoryError(Exception):
    """Base class for repository errors."""


class NotFoundError(RepositoryError):
    """Requested entity was not found (used in delete/update-by-id if you choose to raise)."""


class ForbiddenError(RepositoryError):
    """Requested entity is not permitted by designed policy."""


class ConstraintViolation(RepositoryError):
    """A database constraint was violated."""

    def __init__(self, message: str, *, constraint: str | None = None, column: str | None = None):
        super().__init__(message)
        self.constraint = constraint
        self.column = column


class UniqueViolation(ConstraintViolation):
    """Unique constraint failed."""


class ForeignKeyViolation(ConstraintViolation):
    """Foreign key constraint failed."""


class NotNullViolation(ConstraintViolation):
    """NOT NULL constraint failed."""


class CheckViolation(ConstraintViolation):
    """CHECK constraint failed."""


class EnumViolation(ConstraintViolation):
    """Invalid enum value or similar domain restriction."""


# Optional: a more specific convenience for your common case
class DuplicatePathError(UniqueViolation):
    """Path must be unique."""


class DatabaseLocked(RepositoryError):
    """Raised when the database engine has attempted to make a change that has been rejected for lack of permissions"""


class RecordCannotBeChanged(RepositoryError):
    """Raised when a record cannot be changed"""
