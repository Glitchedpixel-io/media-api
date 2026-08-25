# app/schemas/title_type.py
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ._dynamic import make_partial_model
from .mixins import IDMixin


class TitleTypeAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    code: str = Field(
        ...,
        title="Code",
        description="Short unique code for the title type, e.g. movie",
        max_length=32,
    )
    label: str = Field(..., title="Label", description="Human readable label for the title type")
    description: str | None = Field(
        None, title="Description", description="Helpful information about when to use this type"
    )

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """Normalise the code to lowercase and reject blank values.

        Args:
            v: The submitted code.

        Returns:
            str: The lowercased, stripped code.

        Raises:
            ValueError: If the code is empty or only whitespace.
        """
        if not v or not v.strip():
            raise ValueError("Title type code cannot be empty")
        return v.strip().lower()


class TitleTypeCreatePublic(TitleTypeAttrs):
    pass


class TitleTypeCreateInternal(TitleTypeCreatePublic):
    pass


class TitleTypeRead(TitleTypeCreateInternal, IDMixin):
    pass


TitleTypePatchPublic = make_partial_model(TitleTypeCreatePublic, name="TitleTypePatchPublic")
TitleTypeUpdateInternal = make_partial_model(
    TitleTypeCreateInternal, name="TitleTypeUpdateInternal"
)
