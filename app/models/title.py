# app/models/title.py
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
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

    # Whether this title is something the library grid should offer as an entry point.
    #
    # Independent of whether it is watchable, which is what `type` carries: a standalone
    # film is both, a series is root but not watchable, an episode is watchable but not
    # root, and a season is neither. So rootness needs its own field (#91).
    #
    # Not derived from parent absence, even though the backfill starts there. A curated
    # collection has no parent and is still not a library root, and deriving it would
    # make the grid's every-load filter an anti-join against title_contents.
    #
    # Defaults to false: this is an editorial statement about what the library offers,
    # not a structural fact, and a field that has to be stated should default to
    # unstated. A title wrongly absent is still reachable through the unfiltered list;
    # a wrongly present one degrades the application's most visible surface. The
    # backfill is what states it for everything that already exists.
    library_root: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )

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

    __table_args__ = (
        # The filter the library grid applies on every load, with the sort key it pages
        # by. `id` is both the default sort and the tiebreaker keyset pagination always
        # appends, so a composite serves `WHERE library_root ORDER BY id LIMIT n` as an
        # ordered scan that stops at the page boundary, rather than a bitmap scan
        # followed by a sort of every root.
        #
        # A bare boolean index would not earn its place: roots are the majority of this
        # table, and at that selectivity the planner is right to ignore one. The `id`
        # column is what makes this index worth having -- see the EXPLAIN recorded in
        # the migration.
        Index("ix_titles_library_root_id", "library_root", "id"),
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
