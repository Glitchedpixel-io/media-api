# app/repositories/id_scheme_repository.py
from sqlalchemy import select

from app.models import IdSchemeORM
from app.schemas import (
    IdSchemeCreateInternal,
    IdSchemeRead,
    IdSchemeUpdateInternal,
)

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import IdSchemeRepository


class SQLAlchemyIdSchemeRepository(SQLAlchemyBaseRepository, IdSchemeRepository):
    def create(self, scheme: IdSchemeCreateInternal) -> IdSchemeRead:
        orm = IdSchemeORM(**scheme.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return IdSchemeRead.model_validate(orm, from_attributes=True)

    def get(self, scheme_id: int) -> IdSchemeRead | None:
        orm = self.db.get(IdSchemeORM, scheme_id)
        return IdSchemeRead.model_validate(orm, from_attributes=True) if orm else None

    def exists(self, scheme_id: int) -> bool:
        return self.db.get(IdSchemeORM, scheme_id) is not None

    def get_by_code(self, code: str) -> IdSchemeRead | None:
        stmt = select(IdSchemeORM).where(IdSchemeORM.code == code)
        orm = self.db.scalars(stmt).first()
        return IdSchemeRead.model_validate(orm, from_attributes=True) if orm else None

    def list_all(self) -> list[IdSchemeRead]:
        rows = self.db.scalars(select(IdSchemeORM).order_by(IdSchemeORM.code)).all()
        return [IdSchemeRead.model_validate(row, from_attributes=True) for row in rows]

    def update(self, scheme_id: int, update: IdSchemeUpdateInternal) -> IdSchemeRead:
        orm = self.db.get(IdSchemeORM, scheme_id)
        if not orm:
            raise NotFoundError

        update_data = update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return IdSchemeRead.model_validate(orm, from_attributes=True)
