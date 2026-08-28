"""prevent title self containment

Deletes the rows where a title contains itself, then forbids new ones.

The delete has to come first: 263 such rows existed in production when this was
written, and ``ADD CONSTRAINT`` validates existing rows, so the constraint alone would
fail the deploy rather than protect anything.

Alembic does not compare CHECK constraints, so autogenerate produced an empty
migration and this is hand-written. The matching constraint on ``TitleContentORM``
is what the test suite's ``create_all`` builds from; the two are kept in step by
hand, not by ``alembic check``.

Revision ID: 178b5739d95f
Revises: 5249e7cf3eff
Create Date: 2026-08-28 13:02:39.752830

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "178b5739d95f"
down_revision: Union[str, Sequence[str], None] = "5249e7cf3eff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "no_self_containment_chk"

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    """Remove self-containment rows, then forbid them."""
    # Counted before deleting so the deploy log records what was removed. A data
    # migration that reports nothing leaves no way to tell "there were none" from
    # "it did not run".
    removed = (
        op.get_bind()
        .execute(
            sa.text(
                "DELETE FROM title_contents "
                "WHERE child_title_id IS NOT NULL AND child_title_id = parent_title_id"
            )
        )
        .rowcount
    )
    logger.info("Deleted %s self-containment row(s) from title_contents", removed)

    op.create_check_constraint(
        _CONSTRAINT,
        "title_contents",
        "child_title_id IS DISTINCT FROM parent_title_id",
    )


def downgrade() -> None:
    """Drop the constraint.

    **The deleted rows are not restored.** They recorded a title as its own content,
    which nothing legitimately reads, so there is nothing to put back that a caller
    could want -- but this downgrade is genuinely lossy and saying so is better than
    implying a clean reversal.
    """
    op.drop_constraint(_CONSTRAINT, "title_contents", type_="check")
