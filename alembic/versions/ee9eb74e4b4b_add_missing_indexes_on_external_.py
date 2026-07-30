"""add missing indexes on external_identifiers and tags

Both indexes already exist in production but were never declared in the
SQLAlchemy models, so `alembic check` flags them as drift against a real
snapshot and a fresh `alembic upgrade head` on an empty database never
creates them (issue #11):

    ix_external_identifiers_entity  -> backs ExternalIdentifierRepository.list_for_entity
    ix_tags_name_lower              -> backs TagRepository.get_by_name (case-insensitive)

Revision ID: ee9eb74e4b4b
Revises: c92b391bab4a
Create Date: 2026-07-30 08:46:38.235481

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ee9eb74e4b4b"
down_revision: Union[str, Sequence[str], None] = "c92b391bab4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "ix_external_identifiers_entity",
        "external_identifiers",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index("ix_tags_name_lower", "tags", [sa.literal_column("lower(name)")], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_tags_name_lower", table_name="tags")
    op.drop_index("ix_external_identifiers_entity", table_name="external_identifiers")
