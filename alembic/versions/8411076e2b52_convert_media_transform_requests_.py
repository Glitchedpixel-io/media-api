"""convert media_transform_requests.transform_type to a free-text provider-qualified routing key

`transform_type` on `media_transform_requests` moves from the closed
`transform_type_enum` to `Text`, matching the new `TransformRoutingKey`
schema type (`app/schemas/transform_routing.py`): a validated
`<provider>.<provider-local-type>` string such as `prefect.transcode`,
shape-checked by the API rather than constrained by an allow-list. A new
Prefect deployment (or an entirely new provider) no longer needs a code
release or a migration.

Unlike the preceding rename migrations, this one does not preserve existing
values byte-for-byte -- every row is rewritten from its bare enum label
(e.g. `transcode`) to `prefect.<label>` (e.g. `prefect.transcode`). That is
safe specifically because, until this release, `PrefectDispatcher` was the
*only* consumer of `transform_type` for routing (via the now-removed
`RUNNER_JOB_ROUTING_MAP`), so every pre-existing row was already, implicitly,
a Prefect job -- `prefect.` is the correct provider for all of them, not a
guessed default. The rewrite is injective (`'prefect.' || x` never collides
for distinct `x`), so `uniq_pending_transform_per_asset_and_type` cannot
develop a new collision it didn't already have.

`transform_type_enum` itself is left in place -- `run_summaries` still uses
it (`app/models/run_summary.py`), unrelated to this change -- so there is no
`CREATE TYPE`/`DROP TYPE` here, only the column's type and its rewritten
values.

Revision ID: 8411076e2b52
Revises: 5eab333f4197
Create Date: 2026-07-28 21:57:59.398474

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8411076e2b52"
down_revision: Union[str, Sequence[str], None] = "5eab333f4197"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `create_type=False` renders the bare type name in the generated DDL and
# suppresses any CREATE TYPE/DROP TYPE -- essential since the type is shared
# with `run_summaries`.
TRANSFORM_TYPE_ENUM = postgresql.ENUM(name="transform_type_enum", create_type=False)

_PREFIX = "prefect."
# Postgres substring() is 1-indexed; this is where the provider-local part
# starts, e.g. for "prefect." (8 chars) the local type begins at position 9.
_LOCAL_TYPE_OFFSET = len(_PREFIX) + 1


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "media_transform_requests",
        "transform_type",
        existing_type=TRANSFORM_TYPE_ENUM,
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using=f"'{_PREFIX}' || transform_type::text",
    )


def downgrade() -> None:
    """Downgrade schema."""
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
            FROM media_transform_requests
            WHERE NOT (
                transform_type LIKE '{_PREFIX}%'
                AND substring(transform_type FROM {_LOCAL_TYPE_OFFSET}) IN (
                    SELECT enumlabel FROM pg_enum
                    WHERE enumtypid = 'transform_type_enum'::regtype
                )
            );
            IF offending > 0 THEN
                RAISE EXCEPTION USING MESSAGE =
                    offending || ' row(s) in media_transform_requests have a transform_type '
                    'that is not "{_PREFIX}<original transform_type_enum label>" and cannot be '
                    'mapped back onto transform_type_enum. Update or remove these rows before '
                    'downgrading.';
            END IF;
        END $$;
        """)
    downgrade_using = f"substring(transform_type FROM {_LOCAL_TYPE_OFFSET})::transform_type_enum"
    op.alter_column(
        "media_transform_requests",
        "transform_type",
        existing_type=sa.Text(),
        type_=TRANSFORM_TYPE_ENUM,
        existing_nullable=False,
        postgresql_using=downgrade_using,
    )
