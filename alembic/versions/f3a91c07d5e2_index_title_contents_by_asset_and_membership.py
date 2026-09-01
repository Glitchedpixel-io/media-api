"""index title_contents by asset and membership

Backs the `has_intrinsic_parent` filter on `GET /api/assets/` (#177), which asks
containment from the *asset's* side: does this file have a home, or is it still loose?

Nothing indexed `asset_id` at all before this. `uq_parent_asset_once` is keyed on
(parent_title_id, asset_id), so it answers "what is under this parent" and can never
serve a lookup that pins only the asset -- the same leading-column trap that left the
curated half of the child-side question uncovered until `ix_title_contents_child_membership`
was added in 9a3c5d17be24.

Not the mirror of `uq_one_intrinsic_parent`. An asset may sit under several titles
intrinsically -- the same file under two cuts is ordinary, and the model says so -- so
this cannot be unique, and making it partial on `intrinsic` would leave the filter's
other direction uncovered. A plain composite serves both, and the membership-agnostic
lookup behind `GET /api/assets/{id}/titles` as well.

Measured, and the honest figure is modest. Seeded to the production shape -- 1,585
titles, 13,321 assets, 2,004 containment edges -- the unplaced query is an index-only
scan on this index at 0.458ms against 0.601ms sequentially. Seeded to 100,296 edges the
two are indistinguishable (0.653ms without, 0.695ms with), because *without* this index
the planner does not scan the table: it scans the whole of
`ix_title_contents_child_membership` as a bitmap instead, which is cheap at these sizes.

That fallback is the argument for adding it rather than against. The filter's own key
column is unindexed, so the plan depends on an unrelated index happening to be scannable
in full -- an accident of the current table, not a property the query can rely on as the
curated half grows. This is not the sibling's 34.7ms-to-1.08ms case, and is not claimed
as one.

Revision ID: f3a91c07d5e2
Revises: c5f1a83b6d92
Create Date: 2026-09-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a91c07d5e2"
down_revision: Union[str, Sequence[str], None] = "c5f1a83b6d92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_title_contents_asset_membership"


def upgrade() -> None:
    """Add the composite index backing the asset-side containment filter."""
    op.create_index(_INDEX, "title_contents", ["asset_id", "membership"])


def downgrade() -> None:
    """Drop it. The filter still works, sequentially."""
    op.drop_index(_INDEX, table_name="title_contents")
