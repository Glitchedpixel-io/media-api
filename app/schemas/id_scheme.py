# app/schemas/id_scheme.py
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ._dynamic import make_partial_model
from .enums import EntityTypeEnum
from .mixins import IDMixin
from .utc_basemodel import UTCBaseModel, Timestamp


class IdSchemeAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    code: str = Field(..., title="Code", description="Short unique code for the id scheme")
    label: str = Field(..., title="Label", description="Human readable label for the scheme")
    validator: str | None = Field(
        None,
        title="Validator",
        description="Optional regex or name of validator used to validate ids in this scheme",
    )


class IdSchemeCreatePublic(IdSchemeAttrs):
    pass


class IdSchemeCreateInternal(IdSchemeCreatePublic):
    pass


class IdSchemeRead(IdSchemeCreateInternal, IDMixin):
    pass


class IdSchemePatchPublic(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    code: str | None = Field(None, title="Code", description="Short unique code for the id scheme")
    label: str | None = Field(
        None, title="Label", description="Human readable label for the scheme"
    )
    validator: str | None = Field(
        None,
        title="Validator",
        description="Optional regex or name of validator used to validate ids in this scheme",
    )


class IdSchemeUpdateInternal(IdSchemePatchPublic):
    pass


class AssetIdAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    external_id: str = Field(..., title="External ID", description="External ID of the asset")


class AssetIdCreatePublic(AssetIdAttrs):
    scheme_id: int = Field(..., title="Scheme ID", description="Identifies the external id scheme")


class AssetIdCreateInternal(AssetIdCreatePublic):
    asset_id: int = Field(..., title="Asset ID", description="ID of the asset")


class AssetIdRead(AssetIdCreateInternal, IDMixin):
    pass


class AssetIdReadExtended(AssetIdRead):
    scheme: IdSchemeRead | None = Field(
        None, title="ID Scheme", description="The external id scheme"
    )


class AssetIdPatchPublic(AssetIdAttrs):
    pass


class AssetIdUpdateInternal(AssetIdPatchPublic):
    pass


# ===== External Identifier Schemas (Generic, for both assets and titles) =====


class ExternalIdentifierAttrs(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    external_id: str = Field(..., title="External ID", description="External ID value")


class ExternalIdentifierCreatePublic(ExternalIdentifierAttrs):
    scheme_id: int = Field(..., title="Scheme ID", description="ID of the external ID scheme")


class ExternalIdentifierCreateInternal(ExternalIdentifierCreatePublic):
    entity_type: EntityTypeEnum = Field(..., title="Entity Type", description="Type of entity")
    entity_id: int = Field(..., title="Entity ID", description="ID of the entity")


class ExternalIdentifierRead(ExternalIdentifierCreateInternal, IDMixin):
    created_at: Timestamp = Field(..., title="Created At", description="Timestamp of creation")


class ExternalIdentifierReadExtended(ExternalIdentifierRead):
    scheme: IdSchemeRead | None = Field(
        None, title="ID Scheme", description="The external ID scheme details"
    )


class ExternalIdentifierPatchPublic(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    external_id: str | None = Field(
        None, title="External ID", description="External ID value to update"
    )


class ExternalIdentifierUpdateInternal(ExternalIdentifierPatchPublic):
    pass


# ===== Resolution Response Schema =====


class ExternalIdResolution(BaseModel):
    """Response schema for external ID resolution endpoint."""

    model_config = {"from_attributes": True}

    entity_type: EntityTypeEnum = Field(..., title="Entity Type", description="Type of entity")
    entity_id: int = Field(..., title="Entity ID", description="ID of the entity")
    scheme_code: str = Field(..., title="Scheme Code", description="Code of the scheme")
    external_id: str = Field(..., title="External ID", description="External ID value")
