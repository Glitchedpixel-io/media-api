"""convert run_summaries.transform_type to a free-text provider-qualified routing key and drop transform_type_enum

`transform_type` on `run_summaries` moves from the closed `transform_type_enum`
to `Text`, matching the `TransformRoutingKey` schema type
(`app/schemas/transform_routing.py`) already used by `media_transform_requests`
(see `8411076e2b52`): a validated `<provider>.<provider-local-type>` string
such as `prefect.transcode`.

Like `8411076e2b52`, this rewrites existing values rather than preserving them
byte-for-byte -- every row moves from its bare enum label (e.g. `transcode`)
to `prefect.<label>` (e.g. `prefect.transcode`). That's safe for the same
reason: every run summary recorded before this release came from a worker
processing a `media_transform_requests` row, and `8411076e2b52` already
established that every one of those was implicitly a Prefect job. A literal
`::text` cast would leave dot-less legacy values that fail
`TransformRoutingKey`'s pattern and 500 on every GET of historical data --
this migration avoids that by keeping the two tables' encodings consistent.

`run_summaries` was the last table depending on `transform_type_enum`
(`media_transform_requests` moved off it in `8411076e2b52`, which deliberately
left the type in place for this reason), so this migration also drops the
type once the column conversion is done.

Revision ID: c92b391bab4a
Revises: 8411076e2b52
Create Date: 2026-07-29 09:12:41.639226

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c92b391bab4a"
down_revision: Union[str, Sequence[str], None] = "8411076e2b52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Original label order, exactly as created in the baseline migration -- used
# to recreate the type on downgrade.
_ENUM_LABELS = (
    "extract_audio",
    "test",
    "transcribe",
    "transcode",
    "youtube",
    "whisper_ingest",
    "clipper",
    "stream_reader",
    "ffprobe_metadata",
)

# `create_type=False` suppresses CREATE TYPE/DROP TYPE on the ALTER COLUMN
# calls below -- this migration manages the type's lifecycle explicitly
# instead, since upgrade must DROP TYPE only after the column conversion,
# and downgrade must CREATE TYPE before it.
TRANSFORM_TYPE_ENUM = postgresql.ENUM(name="transform_type_enum", create_type=False)

_PREFIX = "prefect."
# Postgres substring() is 1-indexed; this is where the provider-local part
# starts, e.g. for "prefect." (8 chars) the local type begins at position 9.
_LOCAL_TYPE_OFFSET = len(_PREFIX) + 1


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "run_summaries",
        "transform_type",
        existing_type=TRANSFORM_TYPE_ENUM,
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using=f"'{_PREFIX}' || transform_type::text",
    )
    op.execute("DROP TYPE transform_type_enum")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "CREATE TYPE transform_type_enum AS ENUM ("
        + ", ".join(f"'{label}'" for label in _ENUM_LABELS)
        + ")"
    )
    # Fail clearly, before the cast, if any row can't be mapped back onto
    # transform_type_enum -- e.g. a genuinely new provider-qualified value
    # created since this migration ran. The raw `::transform_type_enum` cast
    # below would fail anyway on such a row, but with an opaque Postgres
    # error; this names the offending count and the remedy instead.
    op.execute(f"""
        DO $$
        DECLARE
            offending integer;
        BEGIN
            SELECT count(*) INTO offending
            FROM run_summaries
            WHERE NOT (
                transform_type LIKE '{_PREFIX}%'
                AND substring(transform_type FROM {_LOCAL_TYPE_OFFSET}) IN (
                    SELECT enumlabel FROM pg_enum
                    WHERE enumtypid = 'transform_type_enum'::regtype
                )
            );
            IF offending > 0 THEN
                RAISE EXCEPTION USING MESSAGE =
                    offending || ' row(s) in run_summaries have a transform_type '
                    'that is not "{_PREFIX}<original transform_type_enum label>" and cannot be '
                    'mapped back onto transform_type_enum. Update or remove these rows before '
                    'downgrading.';
            END IF;
        END $$;
        """)
    downgrade_using = f"substring(transform_type FROM {_LOCAL_TYPE_OFFSET})::transform_type_enum"
    op.alter_column(
        "run_summaries",
        "transform_type",
        existing_type=sa.Text(),
        type_=TRANSFORM_TYPE_ENUM,
        existing_nullable=False,
        postgresql_using=downgrade_using,
    )
