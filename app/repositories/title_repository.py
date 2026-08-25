# app/repositories/title_repository.py
from sqlakeyset import select_page
from sqlalchemy import select
from sqlalchemy.orm import contains_eager, selectinload

from app.models import TitleORM
from app.models.sort_configs import TITLE_SORT
from app.schemas import (
    PageInfo,
    PaginatedResponse,
    TitleCreateInternal,
    TitleListParams,
    TitleRead,
    TitleReadExtended,
    TitleUpdateInternal,
)

from ..utils.sorting import apply_ordering
from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TitleRepository


class SQLAlchemyTitleRepository(SQLAlchemyBaseRepository, TitleRepository):
    def create(self, title: TitleCreateInternal) -> TitleRead:
        orm = TitleORM(**title.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TitleRead.model_validate(orm)

    def get(self, title_id: int) -> TitleRead | None:
        orm = self.db.get(TitleORM, title_id)
        return TitleRead.model_validate(orm) if orm else None

    def get_by_external_id(self, scheme_id: int, external_id: str) -> TitleRead | None:
        stmt = select(TitleORM).where(
            TitleORM.external_ids.any(scheme_id=scheme_id, external_id=external_id)
        )
        title_orm = self.db.execute(stmt).scalar_one_or_none()
        return TitleRead.model_validate(title_orm) if title_orm else None

    def exists(self, title_id: int) -> bool:
        return self.db.get(TitleORM, title_id) is not None

    def list_paged(self, params: TitleListParams) -> PaginatedResponse[TitleReadExtended]:
        # Base selectable. The join to title_types is what lets TITLE_SORT's
        # `title_type` override (which orders by TitleTypeORM.code) resolve.
        # contains_eager reuses that join to populate TitleORM.type instead of
        # letting the relationship's lazy="joined" emit a second one.
        stmt = select(TitleORM).join(TitleORM.type).options(contains_eager(TitleORM.type))

        if params.name:
            stmt = stmt.where(TitleORM.name.ilike(f"%{params.name}%"))

        # Apply sorting
        stmt = apply_ordering(stmt, TITLE_SORT, params.sort)

        # Include optional
        if params.include:
            inclusions = [item.strip().lower() for item in params.include.split(",")]
            if "tags" in inclusions:
                stmt = stmt.options(selectinload(TitleORM.tags))
            if "references" in inclusions:
                stmt = stmt.options(selectinload(TitleORM.references))

        # Use the cursor to fetch the required page
        cursor = params.after or params.before
        page = select_page(self.db, stmt, per_page=params.limit, page=cursor)
        # Read out the results
        rows = [row[0] for row in list(page)]
        items = [TitleReadExtended.model_validate(item) for item in rows]

        return PaginatedResponse[TitleReadExtended](
            items=items,
            page=PageInfo(
                next=self._to_cursor(page.paging.next),
                prev=self._to_cursor(page.paging.previous),
            ),
        )

    def update(self, title_id: int, update: TitleUpdateInternal) -> TitleRead:  # type: ignore
        stmt = select(TitleORM).where(TitleORM.id == title_id)
        orm = self.db.scalar(stmt)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TitleRead.model_validate(orm, from_attributes=True)
