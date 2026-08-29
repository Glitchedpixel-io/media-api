"""rename title type tv to episode

`TV Show` denoted a single episode, so the UI would render "12 TV Shows" when it meant
"12 episodes" and every conversation about the system needed a disambiguating clause
(#93). `Episode` says what the type actually holds.

Cheap, because #41 replaced `title_type_enum` with a `title_types` table (9bdf7126f299):
this is an UPDATE of one row rather than an ALTER TYPE. Nothing in `titles` moves --
`title_type_id` points at the row's id, which does not change -- so no title is
reclassified and no foreign key is touched.

The historical migrations that seed `tv` are deliberately left alone. 9bdf7126f299 and
31d43b7e01c0 record what the vocabulary was when they ran; a migration that rewrote its
own past would make the history a description of the present instead of a record.

Revision ID: 2d7e94fb015a
Revises: 9a3c5d17be24
Create Date: 2026-08-29 15:05:00.000000

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d7e94fb015a"
down_revision: Union[str, Sequence[str], None] = "9a3c5d17be24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = ("tv", "TV")
_NEW = ("episode", "Episode")

logger = logging.getLogger("alembic.runtime.migration")


def _rename(from_code: str, to_code: str, to_label: str) -> None:
    """Rename one title type, tolerating a database that has already moved.

    `title_types.code` is unique, so a blind UPDATE would fail on any deployment where
    the target code already exists -- a contributor database seeded from the current
    models rather than by replaying the history, for instance. Both the absent-source
    and present-target cases are ordinary here, not errors.

    Args:
        from_code: The code being renamed away from.
        to_code: The code being renamed to.
        to_label: The display label to set alongside it.
    """
    bind = op.get_bind()
    target_exists = bind.execute(
        sa.text("SELECT 1 FROM title_types WHERE code = :code"), {"code": to_code}
    ).first()
    if target_exists:
        logger.info("Title type %r already exists; nothing to rename", to_code)
        return

    renamed = bind.execute(
        sa.text(
            "UPDATE title_types SET code = :to_code, label = :to_label WHERE code = :from_code"
        ),
        {"to_code": to_code, "to_label": to_label, "from_code": from_code},
    ).rowcount
    if renamed:
        # Logged with the count because the titles themselves are untouched: this is the
        # only signal in the deploy log that the rename happened at all.
        logger.info("Renamed title type %r to %r (%s row)", from_code, to_code, renamed)
    else:
        logger.info("No title type %r found; nothing to rename", from_code)


def upgrade() -> None:
    """Rename the `tv` title type to `episode`."""
    _rename(_OLD[0], _NEW[0], _NEW[1])


def downgrade() -> None:
    """Rename it back.

    Genuinely reversible, unlike most data migrations here: no row is created or
    destroyed and no title changes type, so the only thing restored is the name.
    """
    _rename(_NEW[0], _OLD[0], _OLD[1])
