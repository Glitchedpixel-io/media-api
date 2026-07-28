# app/schemas/metadata.py
from pydantic import Field

from ._dynamic import make_partial_model
from .mixins import IDMixin
from .utc_basemodel import UTCBaseModel, Timestamp


class MetadataAttr(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    metadata_type: str = Field(..., description="Type of metadata")
    data: dict = Field(..., description="Metadata, stored as JSON")


class MetadataCreatePublic(MetadataAttr):
    pass


class MetadataCreateInternal(MetadataCreatePublic):
    asset_id: int = Field(
        ...,
        title="Asset ID",
        description="ID of the asset to which this metadata belongs",
    )


class MetadataRead(MetadataCreateInternal, IDMixin):
    created_at: Timestamp = Field(..., description="When the record was created")
    updated_at: Timestamp = Field(..., description="When the record was last updated")


MetadataPatchPublic = make_partial_model(MetadataCreatePublic, name="MetadataPatchPublic")

MetadataUpdateInternal = make_partial_model(MetadataCreateInternal, name="MetadataUpdateInternal")
