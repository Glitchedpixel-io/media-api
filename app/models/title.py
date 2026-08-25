# app/models/title.py
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.title_type import TitleTypeORM

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .tag import TagORM
    from .title_reference import TitleReferenceORM
    from .id_scheme import ExternalIdentifierORM


class TitleORM(Base):
    __tablename__ = "titles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title_type_id: Mapped[int] = mapped_column(
        Integer,
        # RESTRICT: a type that is still in use cannot be deleted. TitleTypeService
        # checks usage first so callers get a 409 rather than the 422 a raw
        # ForeignKeyViolation would map to.
        ForeignKey("title_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship to the title's type. lazy="joined" so the `title_type`
    # property below never triggers a lazy load or a DetachedInstanceError once
    # the request's session has closed.
    type: Mapped[TitleTypeORM] = relationship(lazy="joined")

    # Relationship to title references
    references: Mapped[list[TitleReferenceORM]] = relationship(
        "TitleReferenceORM", cascade="all, delete-orphan", lazy="noload"
    )

    # Tags
    tags: Mapped[list[TagORM]] = relationship(
        secondary="title_tags", back_populates="titles", lazy="noload"
    )

    @property
    def title_type(self) -> str:
        """The type's code, which is how a title's type is represented publicly.

        Deliberately read-only, and deliberately not an ``association_proxy``:
        the proxy's setter silently creates a new ``TitleTypeORM`` on assignment.
        Writes go through ``title_type_id``, which the service layer resolves
        from the submitted code.

        Returns:
            str: The code of this title's type, e.g. ``"movie"``.
        """
        return self.type.code

    # External identifiers
    external_ids: Mapped[list[ExternalIdentifierORM]] = relationship(
        back_populates="title",
        primaryjoin="and_(TitleORM.id==foreign(ExternalIdentifierORM.entity_id), ExternalIdentifierORM.entity_type=='title')",
        cascade="all, delete-orphan",
        viewonly=True,
    )
