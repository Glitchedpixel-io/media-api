"""rename videos table and video_id columns to assets and asset_id

Assets were always stored in a table called `videos`, referenced by
`video_id` columns, despite being called "assets" throughout almost all of
the source code and the entire public API surface (`/api/assets`,
`AssetORM`, `AssetRead`, ...). This finishes the job: the DB now matches the
vocabulary everywhere else.

    videos                  -> assets
    streams.video_id        -> streams.asset_id
    media_transform_requests.video_id -> media_transform_requests.asset_id

Renaming the referenced table does not require touching the foreign keys
that point at it (Postgres tracks them by OID, not by name), so those need
no DDL here. A handful of objects were named after the *old* table/column
names at creation time and don't rename themselves automatically:
`master_asset_id`'s implicit index and the composite uniqueness index on
pending transform requests are renamed to keep `alembic check` clean
against the renamed models; `assets.path`'s unique constraint is renamed
too, since `app/repositories/_exc.py` matches on its literal name to
classify duplicate-path errors. Postgres's own auto-generated foreign-key
constraint names (e.g. `streams_video_id_fkey`) are left as-is -- purely
cosmetic, nothing in the app or in `alembic check` depends on those names.

Revision ID: 5eab333f4197
Revises: b2c3d4e5f6a7
Create Date: 2026-07-24 22:06:29.120863

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5eab333f4197"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.rename_table("videos", "assets")
    op.execute("ALTER INDEX ix_videos_master_asset_id RENAME TO ix_assets_master_asset_id")
    op.execute("ALTER TABLE assets RENAME CONSTRAINT videos_path_key TO assets_path_key")

    op.alter_column("streams", "video_id", new_column_name="asset_id")

    op.alter_column("media_transform_requests", "video_id", new_column_name="asset_id")
    op.execute(
        "ALTER INDEX uniq_pending_transform_per_video_and_type "
        "RENAME TO uniq_pending_transform_per_asset_and_type"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER INDEX uniq_pending_transform_per_asset_and_type "
        "RENAME TO uniq_pending_transform_per_video_and_type"
    )
    op.alter_column("media_transform_requests", "asset_id", new_column_name="video_id")

    op.alter_column("streams", "asset_id", new_column_name="video_id")

    op.execute("ALTER TABLE assets RENAME CONSTRAINT assets_path_key TO videos_path_key")
    op.execute("ALTER INDEX ix_assets_master_asset_id RENAME TO ix_videos_master_asset_id")
    op.rename_table("assets", "videos")
