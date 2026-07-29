# app/models/transform_request.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.schemas.enums import OutcomeEnum

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .asset import AssetORM


class TransformRequestORM(Base):
    __tablename__ = "media_transform_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    transform_type: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actioned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    external_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.current_timestamp()
    )
    outcome: Mapped[OutcomeEnum | None] = mapped_column(
        Enum(OutcomeEnum, name="outcome_enum"), nullable=True
    )
    worker: Mapped[str | None] = mapped_column(Text, nullable=True)

    # task chaining
    parent_transform_request_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("media_transform_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    on_success: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    on_failure: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # heartbeat
    first_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship to asset
    asset: Mapped[AssetORM] = relationship("AssetORM", back_populates="transform_requests")

    __table_args__ = (
        CheckConstraint(
            "((actioned = true) AND (processed_at IS NOT NULL) AND (outcome IS NOT NULL)) OR "
            "((actioned = false) AND (processed_at IS NULL) AND (outcome IS NULL))",
            name="check_processed_at_matches_actioned",
        ),
        # Ensure only one pending (actioned=false) transform per asset per transform_type
        Index(
            "uniq_pending_transform_per_asset_and_type",
            "asset_id",
            "transform_type",
            unique=True,
            postgresql_where=text("(actioned = false)"),
            sqlite_where=text("(actioned = 0)"),
        ),
    )
