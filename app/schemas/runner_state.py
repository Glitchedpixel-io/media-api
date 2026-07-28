# app/schemas/runner_state.py
from __future__ import annotations

from typing import Any

from pydantic import Field

from .utc_basemodel import UTCBaseModel, Timestamp


class RunnerStateAttrs(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    state: dict[str, Any] | None = Field(
        None,
        title="Runner state",
        description="Arbitrary JSON blob holding the runner's current state, defined by the runner backend",
    )


class RunnerStateCreatePublic(RunnerStateAttrs):
    pass


class RunnerStateCreateInternal(RunnerStateAttrs):
    runner_key: str = Field(..., title="Runner key", description="Unique key for the runner")


class RunnerStateRead(RunnerStateCreateInternal):
    updated_at: Timestamp = Field(..., description="When the record was last updated")


class RunnerStatePatchPublic(RunnerStateAttrs):
    pass


class RunnerStateUpdateInternal(RunnerStateAttrs):
    pass
