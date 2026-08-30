# app/models/artwork.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.schemas.enums import EntityTypeEnum

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .id_scheme import IdSchemeORM

# The artwork kinds seeded when the table is first created.
#
# As with DEFAULT_TITLE_TYPES, the migration holds its own literal copy rather than
# importing this: a migration records what the schema looked like at a point in time
# and must not change meaning when application code moves on. Adding a kind here does
# NOT add it to an existing database -- create it through the API.
DEFAULT_ARTWORK_KINDS: tuple[tuple[str, str], ...] = (
    ("poster", "Poster"),
    ("backdrop", "Backdrop"),
    ("thumbnail", "Thumbnail"),
    ("logo", "Logo"),
    ("banner", "Banner"),
    ("still", "Still"),
)


class ArtworkKindORM(Base):
    """A kind of artwork, e.g. poster or backdrop.

    A lookup table rather than a native enum, deliberately. ``title_type_enum`` was
    exactly this shape and had to be migrated away in #41 so that adding a type
    became a row edit instead of a schema change -- #93 is cheap as a direct result.
    Artwork kinds will grow the same way (nobody has asked for ``still`` or ``logo``
    yet, and they are already plausible), so starting this as an enum would
    reintroduce a problem this codebase has already solved once.
    """

    __tablename__ = "artwork_kinds"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ArtworkKind(id={self.id}, code='{self.code}')>"


class ArtworkORM(Base):
    """One artwork file, belonging to either a title or an asset.

    Polymorphic over ``(entity_type, entity_id)``, following ``ExternalIdentifierORM``.
    As there, **referential integrity for ``entity_id`` is enforced at the application
    layer**: Postgres cannot express a foreign key whose target table depends on
    another column's value, so nothing at the database level stops a row pointing at
    a title that has since been deleted. The service layer owns that check.

    Artwork belongs to both titles and assets because the data requires it. Only
    1,384 of 13,329 assets are linked to a title at all, while 1,569 of 1,581 titles
    are a parent in ``title_contents`` -- so an asset-only model cannot dress the
    browse grid, and a title-only model discards the artwork that already exists on
    disk next to assets. See #85.
    """

    __tablename__ = "artwork"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    entity_type: Mapped[EntityTypeEnum] = mapped_column(
        Enum(EntityTypeEnum, name="entity_type_enum", create_constraint=True),
        nullable=False,
    )
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)

    artwork_kind_id: Mapped[int] = mapped_column(
        Integer,
        # RESTRICT: a kind still in use cannot be deleted. ArtworkKindService checks
        # usage first so callers get a 409 rather than the 422 a raw
        # ForeignKeyViolation would map to -- the same reasoning as TitleORM.
        ForeignKey("artwork_kinds.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Relative to ARTWORK_ROOT, in the content-addressed layout
    # app.utils.paths.artwork_relative_path computes (#101). Storing the relative
    # path rather than an absolute one keeps rows portable across deployments whose
    # roots differ, which is how `assets.path` already works.
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    # Not nullable since #143. There is no legitimate reason to store -- and later
    # serve -- an image the API knows nothing about, and since #140 every write path
    # measures the bytes, so a null is no longer reachable rather than merely
    # undesirable. The 1,199 rows that predated the measurement were filled in by
    # tools/artwork_dimensions (#115) before the constraint landed.
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Provenance: where this artwork came from. Either a scheme-qualified external ID
    # (the same pair external_identifiers uses) or a plain source URL, or neither for
    # artwork that was simply found on disk -- which is what the #104 backfill will
    # register for every existing cover.
    source_scheme_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("id_schemes.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    kind: Mapped[ArtworkKindORM] = relationship(lazy="joined")
    source_scheme: Mapped[IdSchemeORM | None] = relationship(lazy="joined")

    @property
    def artwork_kind(self) -> str:
        """The kind's code, which is how a kind is represented publicly.

        Read-only, and deliberately not an ``association_proxy`` for the same reason
        ``TitleORM.title_type`` is not: the proxy's setter silently creates a new
        ``ArtworkKindORM`` on assignment. Writes go through ``artwork_kind_id``,
        which the service resolves from the submitted code.

        Returns:
            str: The code of this artwork's kind, e.g. ``"poster"``.
        """
        return self.kind.code

    __table_args__ = (
        # The hot lookup the browse grid drives: "the primary artwork of kind K for
        # entity E". Ordered so the same index also serves the broader
        # "all artwork for entity E" and "artwork of kind K for entity E".
        Index(
            "ix_artwork_entity_kind_primary",
            "entity_type",
            "entity_id",
            "artwork_kind_id",
            "is_primary",
        ),
        # At most one primary per (entity, kind). A partial unique index rather than a
        # constraint because only the `true` rows are constrained -- an entity may
        # hold any number of non-primary posters. Enforced here rather than in the
        # service because two concurrent writers each checking first and then writing
        # is exactly the race #46 recorded for tag-by-name.
        Index(
            "uq_artwork_one_primary_per_kind",
            "entity_type",
            "entity_id",
            "artwork_kind_id",
            unique=True,
            postgresql_where=is_primary.is_(True),
        ),
        # The same file registered twice against one entity is a duplicate, not a
        # second artwork. Content addressing means an identical file always produces
        # an identical storage_path, so this is reachable and worth refusing.
        Index(
            "uq_artwork_entity_storage_path",
            "entity_type",
            "entity_id",
            "storage_path",
            unique=True,
        ),
        # Provenance is a pair or it is nothing. A scheme without an ID cannot be
        # resolved, and an ID without a scheme cannot be interpreted.
        CheckConstraint(
            "(source_scheme_id IS NULL) = (source_external_id IS NULL)",
            name="ck_artwork_source_pair",
        ),
        # The IS NULL branch these carried is gone with the nullability: a constraint
        # that still tolerates a null it can no longer receive misleads the next
        # reader about what the column allows.
        CheckConstraint(
            "width > 0",
            name="ck_artwork_valid_width",
        ),
        CheckConstraint(
            "height > 0",
            name="ck_artwork_valid_height",
        ),
    )
