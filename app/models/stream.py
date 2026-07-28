# app/models/stream.py
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .asset import AssetORM


class StreamORM(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    stream_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec_type: Mapped[str] = mapped_column(Text, nullable=False)
    codec_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_forced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship to asset
    asset: Mapped[AssetORM] = relationship("AssetORM", back_populates="streams")
