"""index title search and sort, and the asset filename extension filter

Substring search cannot use a btree at all, because the patterns carry a leading
wildcard, and `titles.name` had no index even for sorting.

Measured on a scratch schema at 200k titles / 300k assets:

    titles.name ILIKE '%term%'      Seq Scan, 67-72ms
      with ix_titles_name_trgm      Bitmap Heap Scan
        a rare term (5 rows) ....................... 0.5ms
        two words (275 rows) ....................... 1.7ms
        one word (5,812 rows) ...................... 4.3ms
        a common word (9,979 rows) ................. 5.6ms

    assets.path ILIKE '%part%'      Seq Scan, 59.5ms
      with ix_assets_path_trgm      Bitmap Heap Scan, 8.2ms (2.8% of rows)

    titles ORDER BY name LIMIT 50   Seq Scan + Sort, 11.3ms
      with ix_titles_name           Index Scan, 0.15ms

`gin_trgm_ops` matches ILIKE directly, so unlike `ix_assets_path_lower` (#60) the
predicates need no lower() rewrite -- the trigram indexes serve the queries the
repository already issues.

The filename extension filter is the exception, and is deliberately *not* trigram.
`filename ILIKE '%.mkv'` looks like the same shape, but an extension is not a search
term: each one matches about a fifth of the table, and at that selectivity the cost
is dominated by how many rows must be read rather than how they are found.

    assets.filename ILIKE '%.mkv'   Seq Scan, 46.7ms
      via a 16MB trigram index ................... 35.5ms
      via this 2MB functional index ............... 6.6ms
      floor: counting a fifth of the table ........ 9.1ms

So this indexes the extension expression instead, and the predicate in
SQLAlchemyMediaRepository.list_paged is rewritten to match it. As with #60, the
index and the predicate have to change together: either alone leaves the scan in
place. Both sides call `app.models.asset.filename_extension` so they cannot drift.

pg_trgm is a *trusted* extension on PostgreSQL 13+, so the role running this
migration needs CREATE on the database rather than superuser. `app/database.py`
attaches the same CREATE EXTENSION to `Base.metadata` before_create, because tests
build their schema from the models rather than from migrations and would otherwise
fail on the missing operator class.

Revision ID: a1c7f4d2e9b3
Revises: 6b1f8ac340d9
Create Date: 2026-08-29 17:12:44.108227

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c7f4d2e9b3"
down_revision: Union[str, Sequence[str], None] = "6b1f8ac340d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Kept in step with `app.models.asset.FILENAME_EXTENSION_INDEX_SQL`, and spelled the
#: way PostgreSQL stores the expression rather than the way SQLAlchemy would render
#: it. `alembic check` compares the two texts, and `SUBSTRING(x FROM y)` normalises to
#: `"substring"(x, y::text)` -- so the natural spelling fails the drift gate on every
#: run even though the index it builds is correct.
_EXTENSION_EXPR = "lower(\"substring\"(filename, '\\.([^.]+)$'::text))"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_index(
        "ix_titles_name_trgm",
        "titles",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index("ix_titles_name", "titles", ["name"], unique=False)
    op.create_index(
        "ix_assets_path_trgm",
        "assets",
        ["path"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"path": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_assets_filename_ext",
        "assets",
        [sa.literal_column(_EXTENSION_EXPR).label("filename_ext")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_assets_filename_ext", table_name="assets")
    op.drop_index("ix_assets_path_trgm", table_name="assets", postgresql_using="gin")
    op.drop_index("ix_titles_name", table_name="titles")
    op.drop_index("ix_titles_name_trgm", table_name="titles", postgresql_using="gin")
    # pg_trgm is deliberately left installed. Dropping it would fail if anything else
    # in the database uses it, and an unused extension costs nothing.
