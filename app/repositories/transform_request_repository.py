# app/repositories/transform_request_repository.py
from sqlakeyset import select_page
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import TransformRequestORM
from app.models.sort_configs import TRANSFORM_REQUEST_SORT
from app.schemas import (
    PaginatedResponse,
    TransformRequestCreateInternal,
    TransformRequestListParams,
    TransformRequestRead,
    TransformRequestReadExpanded,
    TransformRequestUpdateInternal,
)

from ..utils.sorting import apply_ordering
from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError, RecordCannotBeChanged
from .protocols import TransformRequestRepository


class SQLAlchemyTransformRequestRepository(SQLAlchemyBaseRepository, TransformRequestRepository):
    def create(self, transform_request: TransformRequestCreateInternal) -> TransformRequestRead:
        orm = TransformRequestORM(**transform_request.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TransformRequestRead.model_validate(orm)

    def get(self, request_id: int) -> TransformRequestRead | None:
        orm = self.db.get(TransformRequestORM, request_id)
        return TransformRequestRead.model_validate(orm) if orm else None

    def exists(self, request_id: int) -> bool:
        return self.db.get(TransformRequestORM, request_id) is not None

    def mark_heartbeat(self, request_id: int) -> None:
        stmt = select(TransformRequestORM).where(TransformRequestORM.id == request_id)
        orm: TransformRequestORM | None = self.db.scalar(stmt)
        if orm is None:
            raise NotFoundError
        if orm.actioned:
            raise RecordCannotBeChanged("Cannot heartbeat an actioned transform request")
        if orm.first_heartbeat is None:
            orm.first_heartbeat = func.now()
        orm.last_heartbeat = func.now()
        self._safe_commit()

    def list_paged(
        self, params: TransformRequestListParams
    ) -> PaginatedResponse[TransformRequestReadExpanded]:
        stmt = select(TransformRequestORM)

        # Apply filters
        if params.transform_type is not None:
            stmt = stmt.where(TransformRequestORM.transform_type == params.transform_type)
        if params.actioned is not None:
            stmt = stmt.where(TransformRequestORM.actioned == params.actioned)
        if params.worker_assigned is not None:
            if params.worker_assigned:
                stmt = stmt.where(TransformRequestORM.worker.is_not(None))
            else:
                stmt = stmt.where(TransformRequestORM.worker.is_(None))
        if params.outcome is not None:
            stmt = stmt.where(TransformRequestORM.outcome == params.outcome)

        # Apply sorting
        stmt = apply_ordering(stmt, TRANSFORM_REQUEST_SORT, params.sort)
        stmt = stmt.options(selectinload(TransformRequestORM.asset))

        # Use the cursor to fetch the required page
        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        # Read out the results
        rows = [row[0] for row in list(page)]
        items = [TransformRequestReadExpanded.model_validate(item) for item in rows]

        return PaginatedResponse[TransformRequestReadExpanded](
            items=items,
            page=self._page_info(page),
        )

    def update(
        self,
        request_id: int,
        update: TransformRequestUpdateInternal,  # type: ignore
    ) -> TransformRequestRead:
        stmt = select(TransformRequestORM).where(TransformRequestORM.id == request_id)
        orm = self.db.scalar(stmt)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TransformRequestRead.model_validate(orm, from_attributes=True)

    def get_asset_transform_requests(self, asset_id: int) -> list[TransformRequestRead]:
        rows = self.db.scalars(
            select(TransformRequestORM).where(TransformRequestORM.asset_id == asset_id)
        ).all()
        return [TransformRequestRead.model_validate(row) for row in rows]

    def claim_next(
        self, transform_type: str, worker: str, external_job_id: str | None
    ) -> TransformRequestReadExpanded:
        # Atomically claim the next available task of the given type, eagerly loading asset.
        # Relies on session autobegin (like every other method here) rather than an explicit
        # self.db.begin(), which raises if the session already has an open transaction. The
        # row lock from FOR UPDATE SKIP LOCKED is still held until _safe_commit() below.
        stmt = (
            select(TransformRequestORM)
            .options(selectinload(TransformRequestORM.asset))
            .where(
                TransformRequestORM.transform_type == transform_type,
                TransformRequestORM.actioned == False,  # noqa: E712
                TransformRequestORM.worker.is_(None),
            )
            .order_by(TransformRequestORM.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        orm = self.db.scalars(stmt).first()
        if orm is None:
            raise NotFoundError
        # Assign the worker; do not mark actioned or processed here
        orm.worker = worker
        orm.external_job_id = external_job_id
        self.db.flush()
        result = TransformRequestReadExpanded.model_validate(orm)
        self._safe_commit()
        return result
