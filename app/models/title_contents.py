# app/models/title_contents.py
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.schemas.enums import ContentKind

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from .asset import AssetORM
    from .title import TitleORM


class TitleContentORM(Base):
    __tablename__ = "title_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # The parent title whose list we're ordering
    parent_title_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("titles.id", ondelete="CASCADE"), nullable=False
    )

    # What kind of entry this is
    kind: Mapped[ContentKind] = mapped_column(
        Enum(ContentKind, name="content_kind", native_enum=True), nullable=False
    )

    # Exactly one of these will be set depending on `kind`
    asset_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True
    )
    child_title_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("titles.id", ondelete="RESTRICT"), nullable=True
    )

    # UI-friendly label for the line item
    label: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LexoRank-style key; ensure bytewise/lexicographic ordering (optional: collation="C")
    order_key: Mapped[str] = mapped_column(
        Text().with_variant(postgresql.TEXT(collation="C"), "postgresql"),
        nullable=False,
    )

    # Optional relationships (no back_populates to keep this drop-in)
    parent_title: Mapped[TitleORM] = relationship("TitleORM", foreign_keys=[parent_title_id])
    asset: Mapped[AssetORM] = relationship("AssetORM", foreign_keys=[asset_id])
    child_title: Mapped[TitleORM] = relationship(
        "TitleORM", foreign_keys=[child_title_id], post_update=True
    )

    __table_args__ = (
        # Each parent list must not reuse the same order_key
        UniqueConstraint("parent_title_id", "order_key", name="uq_parent_order"),
        # Enforce “exactly one target” matches the discriminator
        CheckConstraint(
            "("
            "  (kind = 'asset' AND asset_id IS NOT NULL AND child_title_id IS NULL)"
            "  OR"
            "  (kind = 'title' AND child_title_id IS NOT NULL AND asset_id IS NULL)"
            ")",
            name="one_target_chk",
        ),
        # A title cannot contain itself. The shortest possible containment cycle, and
        # the only one the database can rule out declaratively -- reachability needs a
        # recursive walk, which lives in TitleContentService (#88).
        #
        # Worth having even so: 263 of these existed in production, created by a
        # producer that has no idea it is doing it, and every consumer that walks
        # containment would otherwise need its own defence against the shortest case.
        CheckConstraint(
            "child_title_id IS DISTINCT FROM parent_title_id",
            name="no_self_containment_chk",
        ),
        # Prevent duplicate asset entries under the same parent
        Index(
            "uq_parent_asset_once",
            "parent_title_id",
            "asset_id",
            unique=True,
            postgresql_where=(asset_id.isnot(None)),
        ),
        # Prevent duplicate child-title entries under the same parent
        Index(
            "uq_parent_child_title_once",
            "parent_title_id",
            "child_title_id",
            unique=True,
            postgresql_where=(child_title_id.isnot(None)),
        ),
    )
