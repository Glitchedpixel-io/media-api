"""replace title_type_enum with a title_types table

`titles.title_type` moves from the closed `title_type_enum` to
`titles.title_type_id`, a foreign key onto a new `title_types` table. Adding,
renaming, or removing a title type stops being a schema change and becomes an
ordinary row edit through `/api/title_types` (issue #41).

The API contract is unchanged: `title_type` is still a code string such as
`movie` on the way in and out. The service layer resolves that code to an id,
and `TitleORM.title_type` reads it back off the joined row.

`title_types` is seeded with exactly the labels `title_type_enum` carried, so
every existing title maps onto a type with the same code it already had. The
backfill joins on that code and refuses to continue if any row fails to map,
rather than letting the `SET NOT NULL` below fail without saying why.

`_SEED` is a point-in-time copy of `DEFAULT_TITLE_TYPES` in
`app/models/title_type.py`, deliberately duplicated rather than imported: a
migration records what the schema looked like when it was written, and must not
change meaning when application code moves on. The two are expected to diverge
as soon as a type is added through the API -- that is the point of this change.

`titles` was the only table depending on `title_type_enum`, so this migration
also drops the type once the column conversion is done.

Revision ID: 9bdf7126f299
Revises: 08238f77a198
Create Date: 2026-08-25 13:42:51.631361

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9bdf7126f299"
down_revision: Union[str, Sequence[str], None] = "08238f77a198"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Original label order, exactly as created in the baseline migration -- used to
# recreate the type on downgrade, and to seed the table on upgrade.
_ENUM_LABELS = (
    "movie",
    "tv",
    "music",
    "audiobook",
    "event",
    "collection",
    "season",
    "other",
)

# Point-in-time copy of app.models.title_type.DEFAULT_TITLE_TYPES. See the
# module docstring for why this is duplicated rather than imported.
_SEED: tuple[tuple[str, str], ...] = (
    ("movie", "Movie"),
    ("tv", "TV"),
    ("music", "Music"),
    ("audiobook", "Audiobook"),
    ("event", "Event"),
    ("collection", "Collection"),
    ("season", "Season"),
    ("other", "Other"),
)

_FK_NAME = "fk_titles_title_type_id_title_types"

# `create_type=False` suppresses CREATE TYPE/DROP TYPE on the column operations
# below -- this migration manages the type's lifecycle explicitly instead, since
# upgrade must DROP TYPE only after the column is gone, and downgrade must
# CREATE TYPE before the column is added.
TITLE_TYPE_ENUM = postgresql.ENUM(name="title_type_enum", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    title_types = op.create_table(
        "title_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_title_types_code"), "title_types", ["code"], unique=True)

    op.bulk_insert(
        title_types,
        [{"code": code, "label": label, "description": None} for code, label in _SEED],
    )

    # Added nullable so the backfill has somewhere to write; made NOT NULL below.
    op.add_column("titles", sa.Column("title_type_id", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE titles
        SET title_type_id = tt.id
        FROM title_types tt
        WHERE tt.code = titles.title_type::text
        """)

    # Fail clearly, before SET NOT NULL, if any title's enum label has no
    # matching seeded code. SET NOT NULL would fail anyway, but with an opaque
    # Postgres error that names neither the cause nor the remedy.
    op.execute("""
        DO $$
        DECLARE
            unmapped integer;
        BEGIN
            SELECT count(*) INTO unmapped FROM titles WHERE title_type_id IS NULL;
            IF unmapped > 0 THEN
                RAISE EXCEPTION USING MESSAGE =
                    unmapped || ' row(s) in titles have a title_type with no matching '
                    'code in the seeded title_types table. Add the missing type(s) to '
                    'title_types before re-running this migration.';
            END IF;
        END $$;
        """)

    op.alter_column("titles", "title_type_id", existing_type=sa.Integer(), nullable=False)
    op.create_index(op.f("ix_titles_title_type_id"), "titles", ["title_type_id"], unique=False)
    op.create_foreign_key(
        _FK_NAME,
        "titles",
        "title_types",
        ["title_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_column("titles", "title_type")
    op.execute("DROP TYPE title_type_enum")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "CREATE TYPE title_type_enum AS ENUM ("
        + ", ".join(f"'{label}'" for label in _ENUM_LABELS)
        + ")"
    )

    # Fail clearly, before the cast, if any title uses a type created since this
    # migration ran -- exactly what the title_types table exists to allow, and
    # precisely what a closed enum cannot represent. The raw cast below would
    # fail anyway, but without naming the offending count or the remedy.
    op.execute("""
        DO $$
        DECLARE
            offending integer;
        BEGIN
            SELECT count(*) INTO offending
            FROM titles t
            JOIN title_types tt ON tt.id = t.title_type_id
            WHERE tt.code NOT IN (
                SELECT enumlabel FROM pg_enum
                WHERE enumtypid = 'title_type_enum'::regtype
            );
            IF offending > 0 THEN
                RAISE EXCEPTION USING MESSAGE =
                    offending || ' row(s) in titles use a title type that was added after '
                    'title_type_enum was dropped and cannot be mapped back onto it. '
                    'Reassign those titles to one of the original types before downgrading.';
            END IF;
        END $$;
        """)

    op.add_column(
        "titles",
        sa.Column("title_type", TITLE_TYPE_ENUM, autoincrement=False, nullable=True),
    )
    op.execute("""
        UPDATE titles
        SET title_type = tt.code::title_type_enum
        FROM title_types tt
        WHERE tt.id = titles.title_type_id
        """)
    op.alter_column("titles", "title_type", existing_type=TITLE_TYPE_ENUM, nullable=False)

    op.drop_constraint(_FK_NAME, "titles", type_="foreignkey")
    op.drop_index(op.f("ix_titles_title_type_id"), table_name="titles")
    op.drop_column("titles", "title_type_id")

    op.drop_index(op.f("ix_title_types_code"), table_name="title_types")
    op.drop_table("title_types")
