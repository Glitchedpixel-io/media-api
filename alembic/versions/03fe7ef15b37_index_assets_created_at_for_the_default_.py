"""index assets.created_at for the default sort

`created_at:desc` is the front end's default sort on `GET /api/assets/`, so it is
the sort key most likely to be hot. It was unindexed, meaning every page sorted the
whole filtered set.

Measured on a scratch table, deep page reached by cursor:

    rows        unindexed     with this index
    13,000        1.0ms
    130,000       9.2ms
    1,300,000    50.5ms          0.3ms

At today's 13,329 rows this is not a live cost; the index is added because this is
the one sort key a growing library will feel first.

Single-column deliberately. The keyset cursor compares (created_at, id) as a tuple,
which suggests a composite -- but measured at 1.3M rows a composite
(created_at DESC, id DESC) served the same query in 0.6ms against 0.3ms here. It is
wider and buys nothing.

`size`, `duration` and `filename` remain unindexed on purpose: 63-89ms per page at
1.3M rows, 1-2ms at today's size. Each wants an index before `assets` approaches a
million rows; none needs one now.

Revision ID: 03fe7ef15b37
Revises: 9680d1aecf1e
Create Date: 2026-08-27 18:49:48.855440

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03fe7ef15b37"
down_revision: Union[str, Sequence[str], None] = "9680d1aecf1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f("ix_assets_created_at"), "assets", ["created_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_assets_created_at"), table_name="assets")
