# app/repositories/base_repository.py

from sqlakeyset import Marker, Page, serialize_bookmark, unserialize_bookmark
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.schemas import PageInfo

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

    def _safe_flush(self) -> None:
        """Flush pending changes, mapping database errors the way ``_safe_commit`` does.

        Needed wherever a write has to reach the database *before* the transaction ends
        -- reading a list back after a delete, say. Without it the same constraint
        violation surfaces as a raw ``IntegrityError`` from the flush rather than the
        mapped repository error every caller above is written against, purely because
        of where in the unit of work it was noticed.

        :raises IntegrityError: If the flush violates a database integrity constraint.
        :raises DataError: If the flush fails due to invalid data.
        """
        try:
            self.db.flush()
        except (IntegrityError, DataError) as e:
            self.db.rollback()
            raise map_sqla_error(e) from e

    @staticmethod
    def _to_cursor(marker: Marker | None) -> str | None:
        return serialize_bookmark(marker) if marker is not None else None

    @staticmethod
    def _page_info(page: Page) -> PageInfo:
        """Build the cursor pair for a page, honouring the documented null contract.

        ``PageInfo.next`` promises null on the last page, and callers write
        ``while (next)`` loops against that promise. sqlakeyset's ``paging.next`` is a
        marker for "everything after the last row I returned", which it produces
        unconditionally -- on the final page, on a single-page collection, and on an
        empty one. Serialising it directly therefore never yields the null the schema
        advertises, and such a loop runs forever, fetching empty pages.

        ``paging.has_next`` / ``has_previous`` are the questions actually being asked,
        so the marker is only serialised when there is a further page to point at.

        Args:
            page: The page returned by ``sqlakeyset.select_page``.

        Returns:
            PageInfo: Cursors for the adjacent pages; null where none exists.
        """
        paging = page.paging
        return PageInfo(
            next=SQLAlchemyBaseRepository._to_cursor(paging.next) if paging.has_next else None,
            prev=(
                SQLAlchemyBaseRepository._to_cursor(paging.previous)
                if paging.has_previous
                else None
            ),
        )

    @staticmethod
    def _from_cursor(cursor: str | None) -> Marker | None:
        return unserialize_bookmark(cursor) if cursor else None
