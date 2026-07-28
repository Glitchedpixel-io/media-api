# app/schemas/title_contents.py

from pydantic import BaseModel, Field

from app.models.title_contents import ContentKind
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
    pass


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


TitleContentPatchPublic = make_partial_model(TitleContentInsert, name="TitleContentPatchPublic")

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
