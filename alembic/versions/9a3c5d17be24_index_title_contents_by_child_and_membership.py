"""index title_contents by child and membership

Backs the `membership` filter on `GET /api/titles/` (#94), which asks containment from
the child's side and narrows by kind.

`uq_one_intrinsic_parent` already answers the intrinsic half -- it is keyed on
`child_title_id` -- but its predicate is `membership = 'intrinsic'`, so a curated lookup
could not use it and fell back to a sequential scan of the whole table.

Added on a scaled measurement, not a present-day one. At today's 2,155 rows
`title_contents` is a single page and the planner is right to ignore every index on it;
seeded to 100,129 rows the same query is an index-only scan at 1.08ms against a
sequential scan at 34.7ms.

Revision ID: 9a3c5d17be24
Revises: 4f8b21c7ae60
Create Date: 2026-08-29 14:20:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a3c5d17be24"
down_revision: Union[str, Sequence[str], None] = "4f8b21c7ae60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_title_contents_child_membership"


def upgrade() -> None:
    """Add the composite index backing the membership filter."""
    op.create_index(_INDEX, "title_contents", ["child_title_id", "membership"])


def downgrade() -> None:
    """Drop it. The filter still works, sequentially."""
    op.drop_index(_INDEX, table_name="title_contents")
