"""add edition to assets

Sibling assets under one title cover two different things: encodings of the same cut,
which a UI should choose between silently, and different cuts, which it must ask about
(#92). Resolution and codec already describe the first. The second existed only in the
filename, which would have put string matching on the render path of every detail screen.

**No backfill.** Unlike the classifications in 7c4a1e9d2b83 and 4f8b21c7ae60, this column
is left entirely null and populated by a separate reviewed step. The extraction is
guesswork over a filename convention -- a film called "Uncut Gems" is the shape of the
mistake -- and #92 requires a human to read the output before it is applied. A migration
that classified 13,329 assets on its own would make that review impossible to perform
before the fact, and the deploy is the wrong place to hold it.

Run `scripts/extract_editions.py` to produce the report, review it, then apply it. The
script is idempotent and only ever fills nulls.

Revision ID: 6b1f8ac340d9
Revises: 2d7e94fb015a
Create Date: 2026-08-29 16:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b1f8ac340d9"
down_revision: Union[str, Sequence[str], None] = "2d7e94fb015a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable edition column. Nothing is classified here."""
    op.add_column("assets", sa.Column("edition", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop it.

    Lossy in the way that matters: any edition set by review or by hand is discarded,
    and re-running the extraction reproduces the parser's opinion rather than the
    reviewer's corrections.
    """
    op.drop_column("assets", "edition")
