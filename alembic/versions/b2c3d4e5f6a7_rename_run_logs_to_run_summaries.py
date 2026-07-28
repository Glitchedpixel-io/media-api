"""rename run_logs tables to run_summaries

Renames the two worker "run log" tables to "run summaries" to reflect what they
actually store: a structured, low-volume *summary* of each worker run
(processed/success/failed counts, timing, extras), not a line-by-line log
stream. Per-line logs are shipped by workers directly to their own log store, not
through the API, so the "log" name was misleading.

    run_logs_v2       -> run_summaries
    scanner_run_logs  -> scanner_run_summaries

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-22 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.rename_table("run_logs_v2", "run_summaries")
    op.rename_table("scanner_run_logs", "scanner_run_summaries")


def downgrade() -> None:
    """Downgrade schema."""
    op.rename_table("run_summaries", "run_logs_v2")
    op.rename_table("scanner_run_summaries", "scanner_run_logs")
