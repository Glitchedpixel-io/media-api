# app/repositories/title_reference_repository.py
from sqlalchemy import select

from app.models import TitleReferenceORM
from app.schemas import (
    TitleReferenceCreateInternal,
    TitleReferenceRead,
    TitleReferenceUpdateInternal,
)

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TitleReferenceRepository


class SQLAlchemyTitleReferenceRepository(SQLAlchemyBaseRepository, TitleReferenceRepository):
    def create(self, title_reference: TitleReferenceCreateInternal) -> TitleReferenceRead:
        orm = TitleReferenceORM(**title_reference.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TitleReferenceRead.model_validate(orm)

    def get(self, title_reference_id: int) -> TitleReferenceRead | None:
        orm = self.db.get(TitleReferenceORM, title_reference_id)
        return TitleReferenceRead.model_validate(orm) if orm else None

    def exists(self, title_reference_id: int) -> bool:
        return self.db.get(TitleReferenceORM, title_reference_id) is not None

    def list_title_references(self, title_id: int) -> list[TitleReferenceRead]:
        rows = self.db.scalars(
            select(TitleReferenceORM).where(TitleReferenceORM.title_id == title_id)
        ).all()
        return [TitleReferenceRead.model_validate(row) for row in rows]

    def update(
        self,
        title_reference_id: int,
        update: TitleReferenceUpdateInternal,  # type: ignore
    ) -> TitleReferenceRead:
        orm = self.db.get(TitleReferenceORM, title_reference_id)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TitleReferenceRead.model_validate(orm, from_attributes=True)
