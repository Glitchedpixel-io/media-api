"""artwork width and height become NOT NULL

There is no legitimate reason to store -- and eventually serve -- an image the API
knows nothing about. Since #140 every write path measures the bytes and refuses what it
cannot read, so a null is no longer reachable: the upload path takes its dimensions from
`StoredArtwork` (#141), and `tools/artwork_backfill` now does the same rather than
inserting `width=None, height=None`. The nullability was the last thing still permitting
a row that describes nothing.

**No backfill step.** Every existing row already carries dimensions --
`tools/artwork_dimensions` (#115) filled in the 1,199 registered before anything
measured them. The guard below is a check rather than a repair: a null here means a
write path was missed, and repairing it silently inside a deploy would hide that. It
fails with a count and a pointer at the tool instead.

The CHECK constraints are rebuilt at the same time. They read `width IS NULL OR
width > 0`, and a constraint that still tolerates a null the column can no longer hold
misleads the next reader about what is allowed.

Revision ID: d4e7a91c3f28
Revises: a1c7f4d2e9b3
Create Date: 2026-08-30 09:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7a91c3f28"
down_revision: Union[str, Sequence[str], None] = "a1c7f4d2e9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Refuse to run over unmeasured rows, then make both columns NOT NULL."""
    connection = op.get_bind()
    unmeasured = connection.execute(
        sa.text("SELECT count(*) FROM artwork WHERE width IS NULL OR height IS NULL")
    ).scalar_one()
    if unmeasured:
        raise RuntimeError(
            f"{unmeasured} artwork row(s) have no dimensions, so this migration would "
            "fail partway through adding the constraint. Measure them first with "
            "`uv run artwork-dimensions --apply`, then re-run the upgrade. If any row "
            "cannot be measured, its file is unreadable and the row should be removed "
            "rather than kept -- see #143."
        )

    op.alter_column("artwork", "width", existing_type=sa.Integer(), nullable=False)
    op.alter_column("artwork", "height", existing_type=sa.Integer(), nullable=False)

    for column in ("width", "height"):
        op.drop_constraint(f"ck_artwork_valid_{column}", "artwork", type_="check")
        op.create_check_constraint(f"ck_artwork_valid_{column}", "artwork", f"{column} > 0")


def downgrade() -> None:
    """Restore the nullable columns and their null-tolerant checks."""
    for column in ("width", "height"):
        op.drop_constraint(f"ck_artwork_valid_{column}", "artwork", type_="check")
        op.create_check_constraint(
            f"ck_artwork_valid_{column}", "artwork", f"{column} IS NULL OR {column} > 0"
        )

    op.alter_column("artwork", "height", existing_type=sa.Integer(), nullable=True)
    op.alter_column("artwork", "width", existing_type=sa.Integer(), nullable=True)
