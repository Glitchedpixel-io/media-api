"""replace title_contents.order_key with a contiguous integer position

#128. The LexoRank-style `order_key` did not hold its own invariant: the character
midpoint it generated between two neighbours could equal one of them, so a reorder
could produce a key that was not strictly between anything. Measured against a real
list, 7 of 60 random moves failed outright on `uq_parent_order` and 15% of them fell
into the whole-list rebalance the repository used as a fallback, which rewrote every
row's key into something unrecognisable.

The values were unreadable even when it worked. Nothing in production has ever been
reordered, so every key was the result of repeated appends -- a ladder of 'U', 'UU',
'UUU', reaching 35 characters for the 35th entry of the widest list.

Contiguous integers are what the data actually needs. Children per parent measure at a
median of 1, a p95 of 2 and a maximum of 35, so renumbering a list on a move is at most
35 rows; fractional indexing buys O(1) writes for lists this codebase does not have.

The backfill preserves the existing visible order exactly. `order_key` is C-collated and
`ORDER BY order_key` is precisely what the reader used, so `row_number()` over that same
ordering reproduces each list as it stands today.

Revision ID: c5f1a83b6d92
Revises: e8b2c47d9a15
Create Date: 2026-08-30 11:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c5f1a83b6d92"
down_revision: Union[str, Sequence[str], None] = "e8b2c47d9a15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "title_contents"
_OLD_CONSTRAINT = "uq_parent_order"
_NEW_CONSTRAINT = "uq_parent_position"


def upgrade() -> None:
    """Add `position`, backfill it from the existing order, and drop `order_key`."""
    op.add_column(_TABLE, sa.Column("position", sa.Integer(), nullable=True))

    # Zero-based, per parent, in the order the reader currently produces.
    op.execute(f"""
        UPDATE {_TABLE} AS tc
        SET position = ranked.rn
        FROM (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY parent_title_id ORDER BY order_key
                ) - 1 AS rn
            FROM {_TABLE}
        ) AS ranked
        WHERE tc.id = ranked.id
        """)

    op.alter_column(_TABLE, "position", nullable=False)
    op.drop_constraint(_OLD_CONSTRAINT, _TABLE, type_="unique")
    op.drop_column(_TABLE, "order_key")

    # DEFERRABLE INITIALLY DEFERRED: a move renumbers the list in place, and every
    # intermediate state of that renumber has two rows sharing a position. Checking
    # per-statement would reject the shuffle itself; checking at commit asks the
    # question that matters -- is the list well-formed now.
    op.create_unique_constraint(
        _NEW_CONSTRAINT,
        _TABLE,
        ["parent_title_id", "position"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    """Restore `order_key`, rebuilding the ladder the old generator produced.

    `repeat('U', position + 1)` is exactly what the old `tail()` produced for a list
    built by appending, which is how every row in production got its key. It is unique
    per parent and sorts correctly under the C collation the column carries, so the
    restored ordering matches the one being downgraded from.
    """
    op.add_column(
        _TABLE,
        sa.Column(
            "order_key",
            sa.Text().with_variant(postgresql.TEXT(collation="C"), "postgresql"),
            nullable=True,
        ),
    )
    op.execute(f"UPDATE {_TABLE} SET order_key = repeat('U', position + 1)")
    op.alter_column(_TABLE, "order_key", nullable=False)

    op.drop_constraint(_NEW_CONSTRAINT, _TABLE, type_="unique")
    op.drop_column(_TABLE, "position")
    op.create_unique_constraint(_OLD_CONSTRAINT, _TABLE, ["parent_title_id", "order_key"])
