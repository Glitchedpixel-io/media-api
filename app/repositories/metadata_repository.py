# app/repositories/metadata_repository.py
"""
Repository module that implements metadata-related database operations.

This module provides an implementation of a repository pattern for handling
metadata objects, utilizing SQLAlchemy for database interactions. It includes
facilities for creating, retrieving, updating, and deleting metadata records.

Classes:
    SQLAlchemyMetadataRepository: A repository for managing metadata objects
    with SQLAlchemy.
"""

from sqlalchemy import delete, select

from app.models import MetadataORM
from app.schemas import MetadataCreateInternal, MetadataRead, MetadataUpdateInternal

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import MetadataRepository


class SQLAlchemyMetadataRepository(SQLAlchemyBaseRepository, MetadataRepository):
    def get_asset_metadata(self, asset_id: int) -> list[MetadataRead]:
        rows = self.db.scalars(select(MetadataORM).where(MetadataORM.asset_id == asset_id)).all()
        return [MetadataRead.model_validate(row) for row in rows]

    def create(self, metadata: MetadataCreateInternal) -> MetadataRead:
        orm = MetadataORM(**metadata.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return MetadataRead.model_validate(orm)

    def get(self, metadata_id: int) -> MetadataRead | None:
        orm = self.db.get(MetadataORM, metadata_id)
        return MetadataRead.model_validate(orm) if orm else None

    def update(self, metadata_id: int, update: MetadataUpdateInternal) -> MetadataRead:  # type: ignore
        orm = self.db.get(MetadataORM, metadata_id)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return MetadataRead.model_validate(orm, from_attributes=True)

    def delete(self, metadata_id: int) -> None:
        stmt = delete(MetadataORM).where(MetadataORM.id == metadata_id)
        self.db.execute(stmt)
        self._safe_commit()
