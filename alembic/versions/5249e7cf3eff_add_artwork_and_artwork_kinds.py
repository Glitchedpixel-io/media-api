"""add artwork and artwork_kinds

Artwork becomes a first-class entity rather than a `cover.*` file that happens to sit
in an asset's accessory directory (issue #102, decided in #85).

`artwork` is polymorphic over `(entity_type, entity_id)`, following
`external_identifiers`. As there, `entity_id` carries **no foreign key**: its target
table depends on `entity_type`, which Postgres cannot express, so referential
integrity for it is enforced at the application layer.

`artwork_kinds` is a lookup table rather than a native enum. `title_type_enum` was
this exact shape and had to be migrated away in #41 so that adding a kind became a row
edit instead of a schema change. `_SEED` is a point-in-time copy of
`DEFAULT_ARTWORK_KINDS` in `app/models/artwork.py`, deliberately duplicated rather
than imported: a migration records what the schema looked like at a point in time and
must not change meaning when application code moves on.

Nothing backfills `artwork` here. The existing covers on disk are registered by the
#104 backfill, which needs the ARTWORK_ROOT layout from #101 to place them -- a data
migration cannot reach the filesystem those files live on.

Revision ID: 5249e7cf3eff
Revises: 03fe7ef15b37
Create Date: 2026-08-27 20:26:27.082179

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5249e7cf3eff"
down_revision: Union[str, Sequence[str], None] = "03fe7ef15b37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Point-in-time copy of app.models.artwork.DEFAULT_ARTWORK_KINDS. See the module
# docstring for why this is duplicated rather than imported.
_SEED: tuple[tuple[str, str], ...] = (
    ("poster", "Poster"),
    ("backdrop", "Backdrop"),
    ("thumbnail", "Thumbnail"),
    ("logo", "Logo"),
    ("banner", "Banner"),
    ("still", "Still"),
)

# Reference the existing type rather than declaring a new one. `create_type` is a
# postgresql dialect option -- generic `sa.Enum` silently ignores it and emits
# CREATE TYPE anyway, which fails with "type entity_type_enum already exists" on every
# database that has run the baseline. The title_types migration reaches for
# postgresql.ENUM for exactly this reason.
ENTITY_TYPE_ENUM = postgresql.ENUM(name="entity_type_enum", create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    artwork_kinds = op.create_table(
        "artwork_kinds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_artwork_kinds_code"), "artwork_kinds", ["code"], unique=True)

    op.bulk_insert(
        artwork_kinds,
        [{"code": code, "label": label, "description": None} for code, label in _SEED],
    )

    op.create_table(
        "artwork",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # entity_type_enum already exists -- the baseline created it for
        # external_identifiers -- so this column reuses it rather than redeclaring it.
        sa.Column("entity_type", ENTITY_TYPE_ENUM, nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("artwork_kind_id", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("source_scheme_id", sa.Integer(), nullable=True),
        sa.Column("source_external_id", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(source_scheme_id IS NULL) = (source_external_id IS NULL)",
            name="ck_artwork_source_pair",
        ),
        sa.CheckConstraint("height IS NULL OR height > 0", name="ck_artwork_valid_height"),
        sa.CheckConstraint("width IS NULL OR width > 0", name="ck_artwork_valid_width"),
        sa.ForeignKeyConstraint(["artwork_kind_id"], ["artwork_kinds.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_scheme_id"], ["id_schemes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The hot lookup: "the primary artwork of kind K for entity E".
    op.create_index(
        "ix_artwork_entity_kind_primary",
        "artwork",
        ["entity_type", "entity_id", "artwork_kind_id", "is_primary"],
        unique=False,
    )
    op.create_index(
        "uq_artwork_entity_storage_path",
        "artwork",
        ["entity_type", "entity_id", "storage_path"],
        unique=True,
    )
    # Partial: only the primary rows are constrained, so an entity may hold any number
    # of non-primary posters.
    op.create_index(
        "uq_artwork_one_primary_per_kind",
        "artwork",
        ["entity_type", "entity_id", "artwork_kind_id"],
        unique=True,
        postgresql_where=sa.text("is_primary IS true"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_artwork_one_primary_per_kind",
        table_name="artwork",
        postgresql_where=sa.text("is_primary IS true"),
    )
    op.drop_index("uq_artwork_entity_storage_path", table_name="artwork")
    op.drop_index("ix_artwork_entity_kind_primary", table_name="artwork")
    op.drop_table("artwork")
    op.drop_index(op.f("ix_artwork_kinds_code"), table_name="artwork_kinds")
    op.drop_table("artwork_kinds")
    # entity_type_enum is deliberately NOT dropped: external_identifiers still uses it,
    # and the baseline owns its lifecycle.
