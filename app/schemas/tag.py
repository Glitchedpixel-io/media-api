# app/schemas/tag.py
from __future__ import annotations

from pydantic import Field, field_validator

from ._dynamic import make_partial_model
from .mixins import IDMixin
from .utc_basemodel import UTCBaseModel, Timestamp


class TagAttrs(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}
    name: str = Field(
        ...,
        title="Tag name",
        description="Used in the user interface wherever the tag is used",
        max_length=50,
    )
    description: str | None = Field(
        None,
        title="Tag description",
        description="Helpful information about how to use the tag",
        max_length=255,
    )
    color: str = Field(
        default="#6B7280",
        title="Color",
        description="Color used for display",
        max_length=7,
        pattern=r"^#[0-9a-fA-F]{6}$",
    )

    @field_validator("name")
    @classmethod
    def validate_name_not_empty(cls, v: str) -> str:
        """Validate that name is not empty or just whitespace"""
        if not v or v.strip() == "":
            raise ValueError("Tag name cannot be empty")
        return v.lower()


class TagCreatePublic(TagAttrs):
    pass


class TagCreateInternal(TagCreatePublic):
    parent_id: int | None = Field(None, title="Parent ID", description="ID of the parent tag")


class TagRead(TagCreateInternal, IDMixin):
    created_at: Timestamp = Field(..., description="When the record was created")
    updated_at: Timestamp = Field(..., description="When the record was last updated")


TagPatchPublic = make_partial_model(TagCreatePublic, name="TagPatchPublic")

TagUpdateInternal = make_partial_model(TagCreateInternal, name="TagUpdateInternal")


class TagCounts(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}
    tag_id: int = Field(..., title="Tag ID")
    asset_count: int = Field(..., title="Number of assets with this tag")
    title_count: int = Field(..., title="Number of titles with this tag")


class TagSet(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}
    tag_ids: list[int] = Field(
        ...,
        title="Tag IDs",
        description="IDs of the tags this entity should have; replaces the existing set",
    )


class TagNameSet(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}
    tag_names: list[str] = Field(
        ..., title="Tag names", description="Names of the tags to apply to this entity"
    )
    auto_tag_create: bool = Field(
        default=True,
        title="Auto-tag create",
        description="Tags that do not exist will be created.",
    )

    @field_validator("tag_names")
    @classmethod
    def validate_tag_names(cls, v: list[str]) -> list[str]:
        """Validate that each tag name is lowercase"""
        return [v.lower() for v in v]


class TaggingReport(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}
    added_tags: list[TagRead] = Field(
        ..., title="Added Tags", description="Tags added to the asset or title"
    )
    tagging_errors: list[str] = Field(
        ..., title="Tagging Errors", description="Issues encountered while applying tags by name"
    )
