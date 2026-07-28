# app/models/runner_state.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunnerStateORM(Base):
    __tablename__ = "runner_state"

    runner_key: Mapped[str] = mapped_column(Text, primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
    )
    state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
