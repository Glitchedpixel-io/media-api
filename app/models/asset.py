# app/models/asset.py
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ColumnElement,
    ColumnExpressionArgument,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    literal_column,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

#: POSIX pattern capturing a filename's extension: the run of non-dot characters
#: after the final dot. A name with no dot yields NULL, which never equals a
#: requested extension -- the same answer `filename ILIKE '%.ext'` gave.
_EXTENSION_PATTERN = r"\.([^.]+)$"

#: The same expression as :func:`filename_extension`, written the way PostgreSQL
#: stores it, for ``ix_assets_filename_ext`` to be declared with.
#:
#: It cannot be built from ``func`` like the query side is. ``alembic check``
#: compares the model's compiled index expression against what the database reports,
#: and SQLAlchemy renders ``SUBSTRING(x FROM y)`` while PostgreSQL normalises that to
#: ``"substring"(x, y::text)``. The two never match textually, so an otherwise
#: correct index fails CI's drift gate on every run. Measured: only this spelling
#: round-trips unchanged.
#:
#: The column is unqualified because that is how PostgreSQL stores an index
#: expression. The query side qualifies it, which is why the two are not literally
#: the same object -- the planner matches them by parse tree, not by text, and
#: `test_filename_ext_filter_uses_the_index` is what holds that together.
FILENAME_EXTENSION_INDEX_SQL = f"lower(\"substring\"(filename, '{_EXTENSION_PATTERN}'::text))"


def filename_extension(column: ColumnExpressionArgument[str]) -> ColumnElement[str]:
    """The lowercased file extension of a filename column, without its dot.

    Used by the ``filename_ext`` filter, and matched by ``ix_assets_filename_ext``.
    A functional index only serves a query whose expression parses to the same tree,
    so this and :data:`FILENAME_EXTENSION_INDEX_SQL` have to stay in step.

    Args:
        column: The filename column or expression to take the extension of.

    Returns:
        ColumnElement[str]: ``mkv`` for ``Movie.MKV``, NULL for a name with no dot.
    """
    return func.lower(func.substring(column, _EXTENSION_PATTERN))


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

    # Which cut of the work this file is -- theatrical, director's cut -- as opposed to
    # which encoding of it, which resolution and codec already describe. Siblings that
    # differ only in encoding can be chosen between silently; siblings that differ in
    # edition have to be offered to the person (#92).
    #
    # Free text rather than a database enum, and NULL means "no edition marker", not
    # "unrecognised". The two must not collapse: NULL is the licence to pick silently,
    # so folding an unrecognised-but-real edition into it would produce exactly the
    # wrong behaviour for the case this field exists to catch. A marker outside the
    # canonical vocabulary is stored as its own slug instead -- see app/utils/editions.py.
    #
    # A closed enum would also have to be widened by migration each time a distributor
    # invents a cut. This repository has twice moved the other way for open vocabularies:
    # title types became a table (#41) and transform types became free text.
    edition: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    master_asset_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="SET NULL"),
        index=True,  # helpful for list_derived queries
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        # The front end's default sort (created_at:desc), so it is the one sort key
        # most likely to be hot. Measured on 1.3M rows, a deep page costs 50ms
        # unindexed and 0.3ms with this. Single-column is enough even though the
        # keyset cursor compares (created_at, id) -- see ASSET_SORT.
        index=True,
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
        # Backs the path_prefix filter on GET /api/assets/.
        #
        # `text_pattern_ops` is the load-bearing half. This database collates
        # en_US.utf8, and under any non-C collation a btree cannot serve a LIKE
        # prefix at all -- not even a case-sensitive one. Measured at 300k rows:
        # the plain unique index on `path` sequential-scans an ILIKE prefix (51ms),
        # a bare lower(path) index sequential-scans the rewritten predicate too
        # (39ms), and only this one is used (0.4ms, bitmap index scan).
        # The expression is labelled because `postgresql_ops` is keyed by column key
        # or label -- keyed by the rendered expression it is silently ignored, and
        # the index ships without the opclass that makes it work at all.
        Index(
            "ix_assets_path_lower",
            func.lower(path).label("path_lower"),
            postgresql_ops={"path_lower": "text_pattern_ops"},
        ),
        # Backs the path_part filter, which is `path ILIKE '%part%'`.
        #
        # A leading wildcard defeats a btree outright, opclass or not -- so this is
        # trigram rather than a variation on ix_assets_path_lower above, which only
        # serves the anchored path_prefix filter. GIN over GiST: the search is what
        # matters here and GIN answers it faster, at the cost of a slower build.
        #
        # gin_trgm_ops matches ILIKE directly, so unlike the prefix case the
        # predicate needs no lower() rewrite to be index-covered. Measured at 300k
        # rows: 59.5ms sequential against 8.2ms for a term matching 2.8% of the
        # table, and 1.3ms for one matching nothing.
        Index(
            "ix_assets_path_trgm",
            "path",
            postgresql_using="gin",
            postgresql_ops={"path": "gin_trgm_ops"},
        ),
        # Backs the filename_ext filter.
        #
        # Deliberately not a trigram index, though `filename ILIKE '%.mkv'` looks
        # like the same shape as path_part above. An extension is not a search term:
        # every extension matches about a fifth of the table, and at that
        # selectivity the cost is dominated by how many rows must be read rather
        # than by how they are found. Measured at 300k rows: 46.7ms sequential,
        # 35.5ms via a 16MB trigram index, 6.6ms via this 2MB one -- against a 9.1ms
        # floor for reading a fifth of the table with no predicate at all.
        #
        # The filter's predicate is rewritten to match this expression; see
        # `filename_extension`, which both sides import so they cannot drift.
        Index(
            "ix_assets_filename_ext",
            literal_column(FILENAME_EXTENSION_INDEX_SQL).label("filename_ext"),
        ),
    )
