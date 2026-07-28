# app/services/runner_state_service.py
from __future__ import annotations

import uuid

from fastapi import HTTPException

from app.repositories import RunnerStateRepository
from app.schemas import (
    RunnerStateCreateInternal,
    RunnerStateCreatePublic,
    RunnerStatePatchPublic,
    RunnerStateRead,
    RunnerStateUpdateInternal,
)
from app.services.errors import translate_repository_errors


class RunnerStateService:
    def __init__(self, repository: RunnerStateRepository) -> None:
        self.repository = repository

    def get_runner_state(self, runner_key: str) -> RunnerStateRead:
        state = self.repository.get_runner_state(runner_key)
        if not state:
            raise HTTPException(status_code=404, detail="Runner State not found")
        return state

    @translate_repository_errors
    def create_runner_state(self, state: RunnerStateCreatePublic) -> RunnerStateRead:
        if not state.state:
            state.state = {"q": "created by API"}
        return self.repository.create(
            RunnerStateCreateInternal(runner_key=str(uuid.uuid4()), **state.model_dump())
        )

    @translate_repository_errors(not_found_message="Runner state not found")
    def update_runner_state(
        self, runner_key: str, update: RunnerStatePatchPublic, exclude_none: bool
    ) -> RunnerStateRead:
        return self.repository.set_runner_state(
            runner_key,
            RunnerStateUpdateInternal(**update.model_dump(exclude_none=exclude_none)),
        )
