# app/models/artwork.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
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


class ArtworkKindSeed(NamedTuple):
    """One seeded artwork kind, including the shape it expects.

    ``target_ratio`` is width divided by height, and ``ratio_tolerance`` is *relative*
    to it: a ratio passes when ``abs(actual - target) / target <= tolerance``. Relative
    rather than absolute so one tolerance means the same thing at 0.667 as at 1.778.

    Every shape field is optional, because a shape expectation is not universal. A null
    ``target_ratio`` means the kind has no expected shape at all -- which is the honest
    answer for a transparent logo, and for artwork nobody has classified.
    """

    code: str
    label: str
    target_ratio: float | None = None
    ratio_tolerance: float | None = None
    min_width: int | None = None
    max_width: int | None = None


# The artwork kinds seeded when the table is first created.
#
# As with DEFAULT_TITLE_TYPES, the migration holds its own literal copy rather than
# importing this: a migration records what the schema looked like at a point in time
# and must not change meaning when application code moves on. Adding a kind here does
# NOT add it to an existing database -- create it through the API.
#
# **Two of these are measured; the rest are conventions.** `cover_art` and `thumbnail`
# come from the 1,200 rows this repository actually holds (#127): every stored ratio is
# exact except one 499x500 cover, which is why a tolerance exists at all, and a single
# 128x96 row is why a width floor does. Nothing here holds an example of a poster,
# backdrop, still, banner or logo -- their numbers are stated conventions, and #127
# settled that saying so is better than presenting a guess as a measurement. `logo` and
# `banner` therefore expect no shape until something real turns up.
DEFAULT_ARTWORK_KINDS: tuple[ArtworkKindSeed, ...] = (
    # 2:3. The 2% tolerance admits 27:40 theatrical art (0.675) as well as the common
    # 500x750 and 2000x3000 sizes.
    ArtworkKindSeed("poster", "Poster", 2 / 3, 0.02, 300, None),
    ArtworkKindSeed("backdrop", "Backdrop", 16 / 9, 0.02, 1280, None),
    # No target ratio, deliberately and on evidence. The rows this holds are 16:9 and
    # 4:3, both genuine thumbnails of their era, and no tolerance admitting 1.333
    # alongside 1.778 would mean anything -- it would admit almost any shape. Width
    # alone constrains it; the floor sits above the one 128x96 row, which is too small
    # to be useful artwork of any kind.
    ArtworkKindSeed("thumbnail", "Thumbnail", None, None, 320, None),
    ArtworkKindSeed("logo", "Logo", None, None, None, None),
    ArtworkKindSeed("banner", "Banner", None, None, None, None),
    ArtworkKindSeed("still", "Still", 16 / 9, 0.02, 640, None),
    # Square cover art generally -- audiobooks today, music on the same shape. Measured:
    # 132 rows at 500x500 and one at 499x500, the latter 0.2% off and comfortably inside
    # the tolerance that exists for it.
    ArtworkKindSeed("cover_art", "Cover Art", 1.0, 0.02, 300, None),
    # Artwork whose kind nobody credibly declared. No shape expectation by definition:
    # it is the absence of a claim, not a claim about shape. What tools/artwork_backfill
    # registers, since a file found on disk comes with no declaring client.
    ArtworkKindSeed("unknown", "Unknown", None, None, None, None),
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

    # The shape this kind expects (#127). Every field is nullable, because a shape
    # expectation is not universal -- a null target_ratio means "no expected shape",
    # which is the honest answer for a logo and for unclassified artwork, not a gap.
    #
    # Necessary but not sufficient: a client declares the kind and the server checks
    # the pixels do not contradict it. Nothing here is used to *infer* a kind, which is
    # why backdrop, still and thumbnail sharing a shape is not a problem to solve.
    target_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratio_tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_width: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
        # `GET /api/artwork?kind=` with no entity pinned (#182). The composite above
        # cannot serve it: it leads with `entity_type, entity_id`, which that route
        # leaves optional, so there is nothing to enter the index by.
        #
        # **The second column is `id`, and it is what makes this index get used at
        # all.** The route is keyset-paginated, so the query is always
        # `WHERE artwork_kind_id = K [AND id > cursor] ORDER BY id LIMIT n`. Against a
        # bare `(artwork_kind_id)` index the planner correctly prefers walking
        # `artwork_pkey` in id order and filtering, because that avoids a sort -- it
        # made that choice at 1,214 rows, at 100,014 rows, and even with
        # `enable_seqscan = off`. A single-column index here would have been dead
        # weight. `(artwork_kind_id, id)` matches the cursor tuple, so the index
        # supplies the ordering too, which is the shape #62 identified for the asset
        # sort keys and the same reasoning applies here.
        #
        # Measured at 100,014 artwork rows in the production distribution
        # (thumbnail 99,875 / cover_art 133 / poster 5), on `?kind=poster`:
        # **6.947ms walking the primary key, 0.019ms with this index.** The gain is
        # concentrated on the rare kinds, which is exactly where it is wanted: a page
        # of posters is a page the grid asks for and the pkey walk has to cross the
        # whole table to assemble.
        Index(
            "ix_artwork_kind_id",
            "artwork_kind_id",
            "id",
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
