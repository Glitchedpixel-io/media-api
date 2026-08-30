"""artwork kinds carry a shape definition, plus cover_art and unknown

#127 settled that an artwork kind defines a shape: a *necessary but not sufficient*
constraint the server checks against the pixels, never a way to infer a kind. The client
declares what the artwork is; this says whether the claim is contradicted.

Four nullable columns. Nullable is the point rather than convenience -- a null
``target_ratio`` means the kind expects no particular shape, which is the honest answer
for a transparent logo and for artwork nobody has classified, not a gap to be filled in
later.

**Two kinds are seeded from measurement; the rest are conventions, and the difference is
recorded rather than smoothed over.** Across the 1,200 rows this database holds, every
stored ratio is exact except a single 499x500 cover -- which is the entire reason a
tolerance exists -- and a single 128x96 row is the entire reason a width floor does. There
is no example here of a poster, backdrop, still, banner or logo, so their numbers are
stated conventions. Presenting a guess as a derived value would recreate, in a subtler
form, the problem #127 was raised to fix.

Two new kinds:

- ``cover_art`` -- square cover art generally, not audiobook-specific. 133 existing rows
  are square and belong to it.
- ``unknown`` -- artwork whose kind nobody credibly declared, with no shape expectation by
  definition. ``tools/artwork_backfill`` registers this, because a file found on disk
  arrives with no declaring client; asserting a kind it cannot substantiate is what
  produced #138.

Existing rows are **not** reclassified here. That is a separate reviewed step, for the
same reason ``6b1f8ac340d9`` left the edition classification to a script: a deploy is the
wrong place to hold a decision someone should read the output of first.

Revision ID: e8b2c47d9a15
Revises: d4e7a91c3f28
Create Date: 2026-08-30 14:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8b2c47d9a15"
down_revision: Union[str, Sequence[str], None] = "d4e7a91c3f28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Point-in-time copy of app.models.artwork.DEFAULT_ARTWORK_KINDS' shape values. Held
#: literally rather than imported, as the baseline does: a migration records what the
#: schema looked like at a point in time and must not change meaning when application
#: code moves on.
#:
#: (code, label, target_ratio, ratio_tolerance, min_width, max_width)
_SHAPES: tuple[tuple[str, str, float | None, float | None, int | None, int | None], ...] = (
    # Convention. 2% admits 27:40 theatrical art (0.675) alongside the common 2:3 sizes.
    ("poster", "Poster", 2 / 3, 0.02, 300, None),
    ("backdrop", "Backdrop", 16 / 9, 0.02, 1280, None),
    # Measured. Holds both 16:9 and 4:3 rows, so no ratio target is possible: a
    # tolerance admitting 1.333 alongside 1.778 would admit nearly any shape. The floor
    # sits above the one 128x96 row, too small to be useful artwork of any kind.
    ("thumbnail", "Thumbnail", None, None, 320, None),
    ("logo", "Logo", None, None, None, None),
    ("banner", "Banner", None, None, None, None),
    ("still", "Still", 16 / 9, 0.02, 640, None),
)

#: Kinds that do not exist yet on a database created before this revision.
_NEW_KINDS: tuple[tuple[str, str, float | None, float | None, int | None, int | None], ...] = (
    # Measured: 132 rows at 500x500 and one at 499x500, the latter 0.2% off square.
    ("cover_art", "Cover Art", 1.0, 0.02, 300, None),
    ("unknown", "Unknown", None, None, None, None),
)


def upgrade() -> None:
    """Add the shape columns, seed them, and insert the two new kinds."""
    op.add_column("artwork_kinds", sa.Column("target_ratio", sa.Float(), nullable=True))
    op.add_column("artwork_kinds", sa.Column("ratio_tolerance", sa.Float(), nullable=True))
    op.add_column("artwork_kinds", sa.Column("min_width", sa.Integer(), nullable=True))
    op.add_column("artwork_kinds", sa.Column("max_width", sa.Integer(), nullable=True))

    connection = op.get_bind()

    update = sa.text(
        "UPDATE artwork_kinds SET target_ratio = :ratio, ratio_tolerance = :tolerance, "
        "min_width = :min_width, max_width = :max_width WHERE code = :code"
    )
    for code, _label, ratio, tolerance, min_width, max_width in _SHAPES:
        connection.execute(
            update,
            {
                "code": code,
                "ratio": ratio,
                "tolerance": tolerance,
                "min_width": min_width,
                "max_width": max_width,
            },
        )

    # ON CONFLICT because artwork_kinds is a lookup table callers may edit: someone can
    # have created a kind called cover_art through the API before this ran. Not the
    # build-from-scratch path, which reaches here with only the baseline's six.
    insert = sa.text(
        "INSERT INTO artwork_kinds (code, label, target_ratio, ratio_tolerance, min_width, "
        "max_width) VALUES (:code, :label, :ratio, :tolerance, :min_width, :max_width) "
        "ON CONFLICT (code) DO NOTHING"
    )
    for code, label, ratio, tolerance, min_width, max_width in _NEW_KINDS:
        connection.execute(
            insert,
            {
                "code": code,
                "label": label,
                "ratio": ratio,
                "tolerance": tolerance,
                "min_width": min_width,
                "max_width": max_width,
            },
        )


def downgrade() -> None:
    """Drop the shape columns and the two kinds, if nothing is using them.

    A kind still referenced by artwork is left in place: ``artwork.artwork_kind_id`` is
    ``ON DELETE RESTRICT``, so deleting one out from under a row would fail anyway, and
    a downgrade that destroys classification work is worse than one that leaves a row
    behind.
    """
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM artwork_kinds WHERE code IN ('cover_art', 'unknown') "
            "AND id NOT IN (SELECT DISTINCT artwork_kind_id FROM artwork)"
        )
    )

    op.drop_column("artwork_kinds", "max_width")
    op.drop_column("artwork_kinds", "min_width")
    op.drop_column("artwork_kinds", "ratio_tolerance")
    op.drop_column("artwork_kinds", "target_ratio")
