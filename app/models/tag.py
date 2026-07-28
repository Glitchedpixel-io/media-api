# app/models/tag.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import (
    Mapped,
    WriteOnlyMapped,
    mapped_column,
    relationship,
    validates,
)
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .asset import AssetORM
    from .title import TitleORM


class TagORM(Base):
    __tablename__ = "tags"

    # Primary fields
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255))

    # Visual properties
    color: Mapped[str] = mapped_column(String(7), default="#6B7280")

    # Hierarchical structure
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="SET NULL"))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    # Self-referential relationship for hierarchy
    parent: Mapped[TagORM | None] = relationship(
        "TagORM", back_populates="children", remote_side=[id], foreign_keys=[parent_id]
    )
    children: Mapped[list[TagORM]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", foreign_keys=[parent_id]
    )

    # Many-to-many relationships
    assets: WriteOnlyMapped[AssetORM] = relationship(
        secondary="asset_tags", back_populates="tags", passive_deletes=True
    )
    titles: WriteOnlyMapped[TitleORM] = relationship(
        secondary="title_tags", back_populates="tags", passive_deletes=True
    )

    # Indexes for performance
    __table_args__ = (Index("ix_tags_parent_id", "parent_id"),)

    @validates("name")
    def convert_lowercase(self, key: str, value: str) -> str:
        """Automatically convert tag names to lowercase"""
        return value.lower() if value else value

    def __repr__(self) -> str:
        return f"<Tag(id={self.id}, name='{self.name}', parent_id={self.parent_id})>"

    @property
    def full_path(self) -> str:
        """Returns the full hierarchical path, e.g., 'Source/YouTube'"""
        if self.parent:
            return f"{self.parent.full_path}/{self.name}"
        return self.name

    @property
    def depth(self) -> int:
        """Returns the depth level in the hierarchy (root = 0)"""
        if self.parent:
            return self.parent.depth + 1
        return 0

    def get_descendants(self, include_self: bool = False) -> list[TagORM]:
        """Returns all descendant tags (children, grandchildren, etc.)"""
        descendants: list[TagORM] = []
        if include_self:
            descendants.append(self)

        for child in self.children:
            descendants.extend(child.get_descendants(include_self=True))

        return descendants

    def get_ancestors(self, include_self: bool = False) -> list[TagORM]:
        """Returns all ancestor tags (parent, grandparent, etc.)"""
        ancestors: list[TagORM] = []
        if include_self:
            ancestors.append(self)

        if self.parent:
            ancestors.extend(self.parent.get_ancestors(include_self=True))

        return ancestors


class AssetTagORM(Base):
    """Junction table for Asset-Tag many-to-many relationship"""

    __tablename__ = "asset_tags"

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_asset_tags_asset_id", "asset_id"),
        Index("ix_asset_tags_tag_id", "tag_id"),
    )


class TitleTagORM(Base):
    """Junction table for Title-Tag many-to-many relationship"""

    __tablename__ = "title_tags"

    title_id: Mapped[int] = mapped_column(
        ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_title_tags_title_id", "title_id"),
        Index("ix_title_tags_tag_id", "tag_id"),
    )
