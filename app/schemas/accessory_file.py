# app/schemas/accessory_file.py
from pydantic import BaseModel, Field


class AccessoryFile(BaseModel):
    filename: str = Field(
        ...,
        title="Accessory filename",
        description="Name of the accessory file relative to the asset's accessory directory",
    )
    size: int = Field(
        ..., title="Accessory file size", description="Size of the accessory file in bytes"
    )
    mtime: float = Field(
        ...,
        title="Accessory file modification time",
        description="Last-modified time of the accessory file, as a Unix timestamp",
    )
    model_config = {"from_attributes": True}


class AccessoryFilePage(BaseModel):
    items: list[AccessoryFile] = Field(
        ..., title="List of accessory files", description="Accessory files found for the asset"
    )
    asset_id: int = Field(
        ...,
        title="Accessory files belong to this asset",
        description="ID of the asset these accessory files belong to",
    )
    model_config = {"from_attributes": True}
