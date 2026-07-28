# app/schemas/mixins.py
from pydantic import BaseModel, Field


class IDMixin(BaseModel):
    id: int = Field(..., title="Id", description="Unique identifier")
