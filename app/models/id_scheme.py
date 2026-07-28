# app/models/id_scheme.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.database import Base
from app.schemas.enums import EntityTypeEnum

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .asset import AssetORM
    from .title import TitleORM


class IdSchemeORM(Base):
    __tablename__ = "id_schemes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    validator: Mapped[str | None] = mapped_column(Text, nullable=True)

    # backref to all external IDs associated with this scheme
    asset_ids: Mapped[list[AssetIdORM]] = relationship(
        back_populates="scheme",
        cascade="all, delete-orphan",
    )


class AssetIdORM(Base):
    """
    DEPRECATED: Legacy table for asset external IDs only.
    New code should use ExternalIdentifierORM instead.
    This table is kept for backward compatibility but is no longer actively used.
    """

    __tablename__ = "external_asset_ids"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    asset_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    scheme_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("id_schemes.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[str] = mapped_column(Text, nullable=False)

    scheme: Mapped[IdSchemeORM] = relationship(back_populates="asset_ids")

    __table_args__ = (
        # Enforce that an external ID is unique within a scheme
        UniqueConstraint("scheme_id", "external_id", name="uq_scheme_external_id"),
        # Enforce that an asset has at most one ID per scheme
        UniqueConstraint("asset_id", "scheme_id", name="uq_asset_scheme"),
    )


class ExternalIdentifierORM(Base):
    """
    Generic external identifier table supporting both assets and titles.
    Uses typed association pattern (entity_type + entity_id).
    """

    __tablename__ = "external_identifiers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    scheme_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("id_schemes.id", ondelete="CASCADE"), nullable=False
    )

    external_id: Mapped[str] = mapped_column(Text, nullable=False)

    entity_type: Mapped[EntityTypeEnum] = mapped_column(
        Enum(EntityTypeEnum, name="entity_type_enum", create_constraint=True),
        nullable=False,
    )

    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationship to scheme
    scheme: Mapped[IdSchemeORM] = relationship()

    # Relationships to entities (via polymorphic association)
    asset: Mapped[AssetORM | None] = relationship(
        back_populates="external_ids",
        primaryjoin="and_(foreign(ExternalIdentifierORM.entity_id)==AssetORM.id, ExternalIdentifierORM.entity_type=='asset')",
        viewonly=True,
    )
    title: Mapped[TitleORM | None] = relationship(
        back_populates="external_ids",
        primaryjoin="and_(foreign(ExternalIdentifierORM.entity_id)==TitleORM.id, ExternalIdentifierORM.entity_type=='title')",
        viewonly=True,
    )

    __table_args__ = (
        # Enforce that (scheme, external_id) maps to at most one entity
        UniqueConstraint("scheme_id", "external_id", name="uq_external_identifier_scheme_id"),
        # Index for resolution queries (scheme + external_id)
        # Already covered by unique constraint
        # Index for reverse lookups (entity_type + entity_id)
    )

    # Note: Referential integrity for entity_id is enforced at application layer
    # since we cannot create a FK to multiple tables based on entity_type
