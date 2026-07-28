# app/schemas/title_reference.py

from pydantic import BaseModel, Field

from ._dynamic import make_partial_model
from .enums import (
    TitleReferenceTypeEnum,
)
from .mixins import IDMixin


class TitleReferenceAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    reference_type: TitleReferenceTypeEnum = Field(
        ...,
        title="Reference Type",
        description="Category of external reference, e.g. review, metadata, or article",
    )
    reference_url: str = Field(
        ...,
        title="Reference URL",
        description="Link to the external reference article, wiki, review etc...",
    )
    label: str | None = Field(
        None,
        title="Label",
        description="Label to use for the reference in user interfaces",
    )


class TitleReferenceCreatePublic(TitleReferenceAttrs):
    pass


class TitleReferenceCreateInternal(TitleReferenceCreatePublic):
    title_id: int = Field(..., title="Title ID", description="ID of the title")


class TitleReferenceRead(TitleReferenceCreateInternal, IDMixin):
    pass


TitleReferencePatchPublic = make_partial_model(
    TitleReferenceCreatePublic, name="TitleReferencePatchPublic"
)

TitleReferenceUpdateInternal = make_partial_model(
    TitleReferenceCreateInternal, name="TitleReferenceUpdateInternal"
)
