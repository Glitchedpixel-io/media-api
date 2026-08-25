"""allow scanner_run_summaries to record a scan that is not over a filesystem

`scanner_run_summaries` was shaped around a filesystem ingest scanner: nine of
its columns describe a directory walk (`scan_path`, `relative_to_path`,
`total_count`, `folder_count`, `excluded_count`, `error_count`,
`api_error_count`, `no_metadata_count`, `unsupported_file_count`), and all of
them were `NOT NULL`. A scanner over any other kind of source -- a paginated
catalogue, a remote playlist -- has no value for them, and because
`ScannerRunSummaryCreatePublic` also sets `extra: "forbid"`, it could neither
omit them nor record what it *does* count somewhere else. Its only way to post
at all was to send zeros, writing a row that reads as a scan which inspected
nothing (media-api#37).

This drops `NOT NULL` on exactly those nine columns and adds `extras`, the
free-form JSON column `run_summaries` has carried since `b2c3d4e5f6a7`. NULL
now means "this dimension does not apply to this kind of scan", which is a
distinct fact from a measured zero and one no consumer could recover once a
scanner had been forced to write `0`.

The columns describing the scan itself -- `worker_name`, `worker_type`,
`started_at`, `running_time`, `dry_run`, `processed_count`,
`previously_seen_count` -- stay `NOT NULL`. Every scanner can answer those
whatever it scans, so relaxing them would buy nothing and lose a real
constraint.

Upgrade is non-destructive: existing rows keep their values, and any writer
still sending all sixteen fields is unaffected.

**Downgrade refuses rather than backfills.** Restoring `NOT NULL` needs every
NULL gone, and the only way to invent one is `0` or `''` -- which would silently
manufacture the exact false measurement this migration exists to avoid, in rows
a later reader could not tell from genuine ones. So downgrade counts the
offending rows and raises, naming the count and the remedy, in the same spirit
as `c92b391bab4a`'s downgrade guard. Delete or complete those rows first if you
really do mean to go back. `extras` is dropped outright, which does lose data --
that is inherent to reversing a column addition.

Revision ID: 08238f77a198
Revises: ee9eb74e4b4b
Create Date: 2026-08-24 22:45:15.839210

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "08238f77a198"
down_revision: Union[str, Sequence[str], None] = "ee9eb74e4b4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "scanner_run_summaries"

# The nine columns only a filesystem walk can answer, paired with the type each
# keeps across the change -- the alter only touches nullability.
_FILESYSTEM_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("scan_path", sa.TEXT()),
    ("relative_to_path", sa.TEXT()),
    ("total_count", sa.INTEGER()),
    ("folder_count", sa.INTEGER()),
    ("excluded_count", sa.INTEGER()),
    ("error_count", sa.INTEGER()),
    ("api_error_count", sa.INTEGER()),
    ("no_metadata_count", sa.INTEGER()),
    ("unsupported_file_count", sa.INTEGER()),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(_TABLE, sa.Column("extras", sa.JSON(), nullable=True))
    for column, existing_type in _FILESYSTEM_COLUMNS:
        op.alter_column(_TABLE, column, existing_type=existing_type, nullable=True)


def downgrade() -> None:
    """Downgrade schema.

    Raises:
        sqlalchemy.exc.DatabaseError: If any row holds a NULL in a column this
            restores to `NOT NULL`. Backfilling those would fabricate
            measurements, so this refuses instead.
    """
    null_tests = " OR ".join(f"{column} IS NULL" for column, _ in _FILESYSTEM_COLUMNS)
    op.execute(f"""
        DO $$
        DECLARE
            offending integer;
        BEGIN
            SELECT count(*) INTO offending FROM {_TABLE} WHERE {null_tests};
            IF offending > 0 THEN
                RAISE EXCEPTION USING MESSAGE =
                    offending || ' row(s) in {_TABLE} have a NULL in a column that '
                    'this downgrade restores to NOT NULL. Those are scans over a source '
                    'that is not a filesystem, so there is no honest value to backfill -- '
                    'a 0 or an empty string would read as a real measurement. Delete or '
                    'complete these rows before downgrading.';
            END IF;
        END $$;
        """)
    for column, existing_type in reversed(_FILESYSTEM_COLUMNS):
        op.alter_column(_TABLE, column, existing_type=existing_type, nullable=False)
    op.drop_column(_TABLE, "extras")
