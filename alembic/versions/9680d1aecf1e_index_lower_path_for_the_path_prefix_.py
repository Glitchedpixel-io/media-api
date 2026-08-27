"""index lower(path) for the path_prefix filter

`GET /api/assets/?path_prefix=` filtered with `path ILIKE 'x%'`, which no index can
serve, so it sequential-scanned `assets`.

`text_pattern_ops` is not decoration. This database collates en_US.utf8, and under
any non-C collation a btree cannot serve a LIKE prefix at all -- not even a
case-sensitive one. Measured on a scratch schema at 300k rows:

    path ILIKE 'x%'      with assets_path_key ............ Seq Scan, 51ms
    path LIKE  'x%'      with assets_path_key ............ Seq Scan,  7ms
    lower(path) LIKE 'x%' with a plain lower(path) index . Seq Scan, 39ms
    lower(path) LIKE 'x%' with lower(path) text_pattern_ops Bitmap Index Scan, 0.4ms

Only the last is used, so the index and the rewritten predicate in
SQLAlchemyMediaRepository.list_paged have to change together: either alone leaves
the scan in place.

Revision ID: 9680d1aecf1e
Revises: 598c6446db99
Create Date: 2026-08-27 18:41:03.552918

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9680d1aecf1e"
down_revision: Union[str, Sequence[str], None] = "598c6446db99"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_assets_path_lower",
        "assets",
        [sa.literal_column("lower(path)").label("path_lower")],
        unique=False,
        postgresql_ops={"path_lower": "text_pattern_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_assets_path_lower",
        table_name="assets",
        postgresql_ops={"path_lower": "text_pattern_ops"},
    )
