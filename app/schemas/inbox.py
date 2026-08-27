# app/schemas/inbox.py
from __future__ import annotations

import enum

from pydantic import BaseModel, Field, field_validator

from app.utils.paths import to_linux_path


class InboxItemTypeEnum(str, enum.Enum):
    file = "file"
    dir = "dir"


class InboxItem(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    path: str = Field(..., description="Path relative to inbox root")
    name: str = Field(..., description="File or folder name")
    type: InboxItemTypeEnum = Field(..., description="Item type")
    size: int | None = Field(None, description="Size in bytes (files only)")
    children: list[InboxItem] | None = Field(None, description="Children for directories")
    children_truncated: bool = Field(
        False,
        description=(
            "True when this directory's children were not walked, because the depth "
            "limit or the item cap was reached or because it is a symlink. `children` "
            "is then null and says nothing about whether the directory is empty; ask "
            "for a greater depth to see inside it. Always false for files."
        ),
    )

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, v):  # type: ignore
        return to_linux_path(v)


InboxItem.model_rebuild()


class InboxImportRequest(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    source: str = Field(..., description="Path relative to inbox root")
    target: str = Field(
        ...,
        description="Path relative to media root",
    )

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, v):  # type: ignore
        return to_linux_path(v)

    @field_validator("target", mode="before")
    @classmethod
    def _normalize_target(cls, v):  # type: ignore
        return to_linux_path(v)


class InboxDeleteRequest(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    source: str = Field(..., description="Relative path within inbox root to delete (trash)")

    @field_validator("source", mode="before")
    @classmethod
    def _normalize_source(cls, v):  # type: ignore
        return to_linux_path(v)
