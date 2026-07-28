# app/repositories/runner_state_repository.py
from sqlalchemy import select

from app.schemas import (
    RunnerStateCreateInternal,
    RunnerStateRead,
    RunnerStateUpdateInternal,
)

from ..models import RunnerStateORM
from .base_repository import SQLAlchemyBaseRepository
from .errors import NotFoundError
from .protocols import RunnerStateRepository


class SQLAlchemyRunnerStateRepository(SQLAlchemyBaseRepository, RunnerStateRepository):
    def get_runner_state(self, runner_key: str) -> RunnerStateRead | None:
        orm = self.db.get(RunnerStateORM, runner_key)
        return RunnerStateRead.model_validate(orm) if orm else None

    def create(self, state: RunnerStateCreateInternal) -> RunnerStateRead:
        orm = RunnerStateORM(**state.model_dump())
        self.db.add(orm)
        self._safe_commit()
        self.db.refresh(orm)
        return RunnerStateRead.model_validate(orm)

    def set_runner_state(
        self, runner_key: str, update: RunnerStateUpdateInternal
    ) -> RunnerStateRead:
        stmt = select(RunnerStateORM).where(RunnerStateORM.runner_key == runner_key)
        orm = self.db.scalar(stmt)
        if not orm:
            raise NotFoundError

        # Update only fields that were actually provided by the caller
        update_data = update.model_dump(exclude_unset=True)

        for key, value in update_data.items():
            setattr(orm, key, value)

        self._safe_commit()
        self.db.refresh(orm)
        return RunnerStateRead.model_validate(orm, from_attributes=True)
