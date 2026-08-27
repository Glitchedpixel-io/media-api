"""index the foreign keys that scoped reads filter on

Each of these three columns is the sole predicate of an endpoint that reads its
table, and none of them had an index: the tables carried only their primary key,
so every scoped read was a sequential scan.

`external_identifiers.external_id` was considered and deliberately left alone.
`resolve_by_code` joins `id_schemes` on `scheme_id`, which supplies the leading
column of the existing `uq_external_identifier_scheme_id (scheme_id, external_id)`,
so that index already serves the lookup. Measured at 300k rows, adding a
single-column index made the plan marginally worse -- the planner switched to the
narrower index and picked up a join filter.

Revision ID: 598c6446db99
Revises: 9bdf7126f299
Create Date: 2026-08-27 13:52:22.325759

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "598c6446db99"
down_revision: Union[str, Sequence[str], None] = "9bdf7126f299"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f("ix_metadata_asset_id"), "metadata", ["asset_id"], unique=False)
    op.create_index(op.f("ix_streams_asset_id"), "streams", ["asset_id"], unique=False)
    op.create_index(
        op.f("ix_title_references_title_id"), "title_references", ["title_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_title_references_title_id"), table_name="title_references")
    op.drop_index(op.f("ix_streams_asset_id"), table_name="streams")
    op.drop_index(op.f("ix_metadata_asset_id"), table_name="metadata")
