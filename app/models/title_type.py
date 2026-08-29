# app/models/title_type.py
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# The title types seeded when the table is first created.
#
# `alembic/versions/<rev>_replace_title_type_enum_with_title_types_table.py` holds
# its own literal copy of this list rather than importing it: a migration records
# what the schema looked like at a point in time and must not change meaning when
# application code moves on. Adding a type here does NOT add it to an existing
# database -- create it through `POST /api/title_types`, which is the whole point
# of this table replacing the old `title_type_enum` (issue #41).
DEFAULT_TITLE_TYPES: tuple[tuple[str, str], ...] = (
    ("movie", "Movie"),
    ("episode", "Episode"),
    ("music", "Music"),
    ("audiobook", "Audiobook"),
    ("event", "Event"),
    ("collection", "Collection"),
    ("season", "Season"),
    ("other", "Other"),
)


class TitleTypeORM(Base):
    """A kind of title, e.g. movie or season.

    Replaces the former ``title_type_enum`` Postgres enum so that types can be
    added, renamed, and removed at runtime instead of by migration.
    """

    __tablename__ = "title_types"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<TitleType(id={self.id}, code='{self.code}')>"
