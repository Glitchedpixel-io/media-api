# app/schemas/title_contents.py

from pydantic import BaseModel, Field

from app.models.title_contents import ContentKind, MembershipKind
from app.schemas import AssetRead, TitleRead
from .mixins import IDMixin

from ._dynamic import make_partial_model


class TitleContentAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    kind: ContentKind = Field(
        ...,
        title="Content Kind",
        description="Identifies whether this item is a reference to a Title or an Asset",
    )
    child_title_id: int | None = Field(
        None, title="Title ID", description="ID of the associated Title"
    )
    asset_id: int | None = Field(None, title="Asset ID", description="ID of the associated Asset")
    label: str | None = Field(
        None, title="Label", description="UI friendly label for this piece of content"
    )


class TitleContentInsert(TitleContentAttrs):
    membership: MembershipKind = Field(
        MembershipKind.intrinsic,
        title="Membership",
        description=(
            "Whether this row is the child's home (intrinsic) or a curated list it also "
            "appears in (curated). Intrinsic parentage drives breadcrumbs and is limited "
            "to one per child; curated membership is unlimited."
        ),
    )


class TitleContentCreateInternal(TitleContentInsert):
    parent_title_id: int = Field(
        ...,
        title="Parent Title ID",
        description="ID of the parent Title to which this piece of content belongs",
    )
    position: int = Field(
        ...,
        title="Position",
        description=(
            "This item's place in its parent's ordered content list: zero-based, "
            "contiguous and ascending. Positions are assigned by the API, so a move "
            "renumbers the rows it displaces; they are not stable identifiers."
        ),
    )


class TitleContentRead(TitleContentCreateInternal, IDMixin):
    pass


# Built from TitleContentAttrs rather than TitleContentInsert so that `membership` is
# absent from the patch model entirely. The requirement is asymmetric: a curated
# membership must be settable, which is the whole feature, while an intrinsic one must
# not be casually flipped -- moving an item's home is a different operation from
# editing a list entry, and should not be reachable by a field on a PATCH body.
#
# Absence is the enforcement. TitleContentAttrs forbids extra fields, so a PATCH
# carrying `membership` is rejected by the schema with a 422 before any service code
# runs, rather than being silently ignored. `position` is kept out of reach the same
# way -- reordering has its own endpoint.
TitleContentPatchPublic = make_partial_model(TitleContentAttrs, name="TitleContentPatchPublic")

TitleContentUpdateInternal = make_partial_model(
    TitleContentCreateInternal, name="TitleContentUpdateInternal"
)


#: Most entries one batch write may carry (#179).
#:
#: Chosen from the data, not rounded to a comfortable-looking number. The placement
#: queue is 11,945 assets spread over 1,966 directories, and a directory is the unit of
#: the gesture: median 14, p95 796, largest 796. A cap of 1,000 therefore places every
#: directory currently on disk in a single request, which is the property worth having --
#: a cap that splits the two largest folders makes the interface handle paging for the
#: exact cases bulk exists to serve.
#:
#: Measured at the ceiling, against a database seeded to the production shape: a batch of
#: 1,000 holds the parent title lock for **368ms** and takes 1.2s in total. The lock is
#: the number that matters, because since #193 it blocks every other write to that
#: parent, and validation -- which is the larger half, at 877ms of recursive cycle walks
#: -- happens before the lock is taken, not under it.
MAX_BATCH_ITEMS = 1_000


class TitleContentBatchInsert(BaseModel):
    """Several entries to append to one parent, applied as one transaction."""

    model_config = {"extra": "forbid"}

    items: list[TitleContentInsert] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_ITEMS,
        title="Items",
        description=(
            "The entries to append, in the order they should land. Applied "
            "all-or-nothing: if any item is invalid nothing is written, and the error "
            "names every item that failed rather than the first"
        ),
    )


class TitleContentBatchIds(BaseModel):
    """Several existing entries to act on, applied as one transaction."""

    model_config = {"extra": "forbid"}

    title_contents_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_ITEMS,
        title="Title Content IDs",
        description=(
            "The containment rows to act on. Repeats are collapsed. Applied "
            "all-or-nothing: if any id is not usable nothing is written, and the error "
            "names every one that failed"
        ),
    )


class TitleContentBatchResult(BaseModel):
    """What a batch write did.

    Deliberately not a per-item status list. The write is all-or-nothing, so on success
    every item succeeded and a per-item report would be a column of identical ticks;
    the interesting per-item detail belongs in the *error* body, where it says which
    items stopped the batch. See the router docstrings for that shape.
    """

    model_config = {"from_attributes": True}

    count: int = Field(..., title="Count", description="How many rows the batch affected")
    items: list[TitleContentRead] = Field(
        default_factory=list,
        title="Items",
        description=(
            "The affected rows, in the order given. Empty for a detach, which removes " "them"
        ),
    )


class TitleContentReadParent(TitleContentRead):
    parent_title: TitleRead = Field(
        ..., title="Parent Title", description="The Title that owns this piece of content"
    )


class TitleContentReadExtended(TitleContentRead):
    asset: AssetRead | None = Field(
        None, title="Asset", description="Asset backing this item of content, if kind is asset"
    )
    child_title: TitleRead | None = Field(
        None,
        title="Child Title",
        description="Title backing this item of content, if kind is title",
    )


class TitleContentCounts(BaseModel):
    """How many titles and assets a title directly contains.

    Direct edges only -- nothing beneath the children is counted. Membership is
    deliberately not filtered: a curated list's whole purpose is the things in it,
    so counting intrinsic edges only would report every curated collection as empty.

    No deduplication is applied because none is needed. ``uq_parent_child_title_once``
    and ``uq_parent_asset_once`` are unique on (parent, target) with membership outside
    their predicates, so a target cannot appear twice under one parent whatever its
    membership. Dedup is load-bearing for ``TitleMediaTotals``, not here.
    """

    model_config = {"from_attributes": True}

    child_count: int = Field(0, title="Child Title Count", description="Titles directly contained")
    asset_count: int = Field(0, title="Asset Count", description="Assets directly contained")


class TitleMediaTotals(BaseModel):
    """Combined runtime and size of every distinct asset beneath a title.

    Follows intrinsic containment only, so a title borrowed into a curated list does
    not add its runtime to that list's total.
    """

    model_config = {"from_attributes": True}

    total_runtime: float = Field(
        0.0, title="Total Runtime", description="Combined duration in seconds"
    )
    total_size: int = Field(0, title="Total Size", description="Combined size in bytes")
