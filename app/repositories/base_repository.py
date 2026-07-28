# app/repositories/base_repository.py

from sqlakeyset import Marker, serialize_bookmark, unserialize_bookmark
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from ._exc import map_sqla_error


class SQLAlchemyBaseRepository:
    """
    Base repository class for handling database interactions using SQLAlchemy.

    This class serves as a foundational repository that provides common functionality
    for database operations, including session handling and transaction management.
    It abstracts common patterns for interacting with a SQLAlchemy session.

    :ivar db: SQLAlchemy session for managing database transactions and interactions.
    :type db: Session
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _safe_commit(self) -> None:
        """
        Commits the current transaction to the database safely. If an error occurs during the commit
        process, it rolls back the transaction and raises a mapped SQLAlchemy error.

        :raises IntegrityError: If the commit violates a database integrity constraint.
        :raises DataError: If the commit fails due to invalid data, exceeding field lengths,
            or other data-related issues.
        """
        try:
            self.db.commit()
        except (IntegrityError, DataError) as e:
            self.db.rollback()
            raise map_sqla_error(e) from e

    @staticmethod
    def _to_cursor(marker: Marker | None) -> str | None:
        return serialize_bookmark(marker) if marker is not None else None

    @staticmethod
    def _from_cursor(cursor: str | None) -> Marker | None:
        return unserialize_bookmark(cursor) if cursor else None
