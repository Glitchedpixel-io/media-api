# app/repositories/stream_repository.py
from sqlalchemy import delete, select

from app.models import StreamORM
from app.schemas import StreamCreateInternal, StreamRead, StreamUpdateInternal

from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import StreamRepository


class SQLAlchemyStreamRepository(SQLAlchemyBaseRepository, StreamRepository):
    def create(self, stream: StreamCreateInternal) -> StreamRead:
        orm = StreamORM(**stream.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return StreamRead.model_validate(orm)

    def get(self, stream_id: int) -> StreamRead | None:
        orm = self.db.get(StreamORM, stream_id)
        return StreamRead.model_validate(orm) if orm else None

    def exists(self, stream_id: int) -> bool:
        return self.db.get(StreamORM, stream_id) is not None

    def list_all(self) -> list[StreamRead]:
        rows = self.db.scalars(select(StreamORM)).all()
        return [StreamRead.model_validate(row) for row in rows]

    def update(self, stream_id: int, update: StreamUpdateInternal) -> StreamRead:  # type: ignore
        orm = self.db.get(StreamORM, stream_id)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)  # type: ignore

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return StreamRead.model_validate(orm, from_attributes=True)

    def get_asset_streams(self, asset_id: int) -> list[StreamRead]:
        rows = self.db.scalars(select(StreamORM).where(StreamORM.asset_id == asset_id)).all()
        return [StreamRead.model_validate(row) for row in rows]

    def delete_asset_streams(self, asset_id: int) -> None:
        stmt = delete(StreamORM).where(StreamORM.asset_id == asset_id)
        self.db.execute(stmt)
        self._safe_commit()
