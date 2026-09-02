"""index the two uncovered kind filters

`title_contents.membership` and `artwork.artwork_kind_id` both back filters the report
records as sequential scans (#182). Both are now indexed -- but **neither as a
single-column index**, because measurement showed a bare column would not be used.

`ix_title_contents_membership_child (membership, child_title_id)`
    `GET /api/titles/?membership=` with no parent pinned has to find every edge of a
    kind rather than check one child's edges, so it needs an index leading with
    `membership`. Carrying `child_title_id` second makes the semi-join index-only,
    since that is the only column it wants. At the production shape (1,917 edges, 119
    curated), on `?membership=curated`: sequential scan cost 38.96, index scan on
    membership alone 10.36, index-only scan on the pair **6.36**.

    Not a duplicate of `ix_title_contents_child_membership`, which holds the same two
    columns in the other order: that one answers "what kind of edge does this child
    have", this one answers "which children have an edge of this kind", and neither
    serves the other's question.

`ix_artwork_kind_id (artwork_kind_id, id)`
    The `id` is load-bearing. `GET /api/artwork?kind=` is keyset-paginated, so the
    query is always `WHERE artwork_kind_id = K [AND id > cursor] ORDER BY id LIMIT n`.
    Against a bare `(artwork_kind_id)` index the planner prefers walking `artwork_pkey`
    in id order and filtering, because that avoids a sort -- verified at 1,214 rows, at
    100,014 rows, and with `enable_seqscan = off`. The single-column index would have
    been dead weight. The pair matches the cursor tuple, so the index supplies the
    ordering as well.

    Measured at 100,014 rows in the production distribution, on `?kind=poster` (5 rows):
    **6.947ms against 0.019ms.**

What this migration deliberately does *not* claim: `resolves_display_image` is
unaffected. Its seed is `artwork_kind_id IN (<the display chain>)`, and the chain covers
1,213 of 1,214 artwork rows in production -- an index cannot narrow a predicate that
matches everything, and the plans are identical with and without. The issue named that
filter as a reason to index this column; it is not one.

Revision ID: b7d2e4a91f38
Revises: f3a91c07d5e2
Create Date: 2026-09-01 23:40:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e4a91f38"
down_revision: Union[str, Sequence[str], None] = "f3a91c07d5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the two composites backing the kind filters."""
    op.create_index(
        "ix_title_contents_membership_child",
        "title_contents",
        ["membership", "child_title_id"],
    )
    op.create_index("ix_artwork_kind_id", "artwork", ["artwork_kind_id", "id"])


def downgrade() -> None:
    """Drop them. Both filters still work, sequentially."""
    op.drop_index("ix_artwork_kind_id", table_name="artwork")
    op.drop_index("ix_title_contents_membership_child", table_name="title_contents")
