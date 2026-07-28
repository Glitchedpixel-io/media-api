"""rename flow_run_id to external_job_id

Renames media_transform_requests.flow_run_id -> external_job_id to de-brand the
public API contract: the column stores a backend-assigned job reference, not a
Prefect-specific flow run id.

Revision ID: a1b2c3d4e5f6
Revises: 31d43b7e01c0
Create Date: 2026-07-22 10:05:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "31d43b7e01c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("media_transform_requests") as batch_op:
        batch_op.alter_column("flow_run_id", new_column_name="external_job_id")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("media_transform_requests") as batch_op:
        batch_op.alter_column("external_job_id", new_column_name="flow_run_id")
