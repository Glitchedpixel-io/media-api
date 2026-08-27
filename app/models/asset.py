# app/models/asset.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .id_scheme import ExternalIdentifierORM
    from .metadata import MetadataORM
    from .stream import StreamORM
    from .tag import TagORM
    from .transform_request import TransformRequestORM


class AssetORM(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    duration: Mapped[float] = mapped_column(Numeric, nullable=False)
    bitrate: Mapped[int] = mapped_column(BigInteger, nullable=False)
    container_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    master_asset_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,  # helpful for list_derived queries
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship to streams
    streams: Mapped[list[StreamORM]] = relationship(
        "StreamORM", back_populates="asset", cascade="all, delete-orphan"
    )
    # Relationship to metadata
    asset_metadata: Mapped[list[MetadataORM]] = relationship(
        "MetadataORM", back_populates="asset", cascade="all, delete-orphan"
    )
    # Relationship to transform requests
    transform_requests: Mapped[list[TransformRequestORM]] = relationship(
        "TransformRequestORM", back_populates="asset", cascade="all, delete-orphan"
    )

    # Relationship to master asset (self-referential)
    master_asset: Mapped[AssetORM | None] = relationship(
        "AssetORM",
        remote_side=[id],
        foreign_keys=[master_asset_id],
        primaryjoin="AssetORM.master_asset_id==AssetORM.id",
        uselist=False,
        lazy="noload",
    )

    # Tags
    tags: Mapped[list[TagORM]] = relationship(
        secondary="asset_tags", back_populates="assets", lazy="noload"
    )

    # Eager by default: AssetReadExtended serialises external_ids unconditionally, so a
    # lazy load here costs one SELECT per row returned. Unlike tags and master_asset,
    # this field is not gated behind include=, so there is no request that wants it unloaded.
    external_ids: Mapped[list[ExternalIdentifierORM]] = relationship(
        back_populates="asset",
        primaryjoin="and_(AssetORM.id==foreign(ExternalIdentifierORM.entity_id), ExternalIdentifierORM.entity_type=='asset')",
        cascade="all, delete-orphan",
        viewonly=True,
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "master_asset_id IS NULL OR master_asset_id <> id",
            name="ck_asset_not_own_master",
        ),
        CheckConstraint(
            "duration >= 0",
            name="ck_asset_valid_duration",
        ),
        CheckConstraint(
            "bitrate >= 0",
            name="ck_asset_valid_bitrate",
        ),
        CheckConstraint(
            "size >= 0",
            name="ck_asset_valid_size",
        ),
    )
