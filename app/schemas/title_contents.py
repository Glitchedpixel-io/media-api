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
    order_key: str = Field(
        ...,
        title="Order Key",
        description="Lexicographically sortable key controlling this item's position within its parent's ordered content list",
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
# runs, rather than being silently ignored. `order_key` is kept out of reach the same
# way -- reordering has its own endpoint.
TitleContentPatchPublic = make_partial_model(TitleContentAttrs, name="TitleContentPatchPublic")

TitleContentUpdateInternal = make_partial_model(
    TitleContentCreateInternal, name="TitleContentUpdateInternal"
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
