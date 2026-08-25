# app/repositories/title_type_repository.py
from sqlalchemy import func, select

from app.models import TitleORM, TitleTypeORM
from app.schemas import (
    TitleTypeCreateInternal,
    TitleTypeRead,
    TitleTypeUpdateInternal,
)

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import TitleTypeRepository


class SQLAlchemyTitleTypeRepository(SQLAlchemyBaseRepository, TitleTypeRepository):
    def create(self, title_type: TitleTypeCreateInternal) -> TitleTypeRead:
        orm = TitleTypeORM(**title_type.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return TitleTypeRead.model_validate(orm, from_attributes=True)

    def get(self, title_type_id: int) -> TitleTypeRead | None:
        orm = self.db.get(TitleTypeORM, title_type_id)
        return TitleTypeRead.model_validate(orm, from_attributes=True) if orm else None

    def exists(self, title_type_id: int) -> bool:
        return self.db.get(TitleTypeORM, title_type_id) is not None

    def get_by_code(self, code: str) -> TitleTypeRead | None:
        stmt = select(TitleTypeORM).where(TitleTypeORM.code == code)
        orm = self.db.scalars(stmt).first()
        return TitleTypeRead.model_validate(orm, from_attributes=True) if orm else None

    def list_all(self) -> list[TitleTypeRead]:
        rows = self.db.scalars(select(TitleTypeORM).order_by(TitleTypeORM.code)).all()
        return [TitleTypeRead.model_validate(row, from_attributes=True) for row in rows]

    def update(
        self,
        title_type_id: int,
        update: TitleTypeUpdateInternal,  # type: ignore
    ) -> TitleTypeRead:
        orm = self.db.get(TitleTypeORM, title_type_id)
        if not orm:
            raise NotFoundError

        update_data = update.model_dump(exclude_unset=True)  # type: ignore
        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return TitleTypeRead.model_validate(orm, from_attributes=True)

    def delete(self, title_type_id: int) -> None:
        orm = self.db.get(TitleTypeORM, title_type_id)
        if not orm:
            raise NotFoundError
        self.db.delete(orm)
        self._safe_commit()

    def usage_count(self, title_type_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(TitleORM)
            .where(TitleORM.title_type_id == title_type_id)
        )
        return self.db.scalar(stmt) or 0
