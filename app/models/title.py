# app/models/title.py
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Enum,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.schemas.enums import TitleTypeEnum

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .tag import TagORM
    from .title_reference import TitleReferenceORM
    from .id_scheme import ExternalIdentifierORM


class TitleORM(Base):
    __tablename__ = "titles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title_type: Mapped[TitleTypeEnum] = mapped_column(
        Enum(TitleTypeEnum, name="title_type_enum"), nullable=False
    )
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship to title references
    references: Mapped[list[TitleReferenceORM]] = relationship(
        "TitleReferenceORM", cascade="all, delete-orphan", lazy="noload"
    )

    # Tags
    tags: Mapped[list[TagORM]] = relationship(
        secondary="title_tags", back_populates="titles", lazy="noload"
    )

    # External identifiers
    external_ids: Mapped[list[ExternalIdentifierORM]] = relationship(
        back_populates="title",
        primaryjoin="and_(TitleORM.id==foreign(ExternalIdentifierORM.entity_id), ExternalIdentifierORM.entity_type=='title')",
        cascade="all, delete-orphan",
        viewonly=True,
    )
