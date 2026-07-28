# app/schemas/stream.py

from pydantic import BaseModel, Field

from ._dynamic import make_partial_model
from .mixins import IDMixin


class StreamAttrs(BaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    stream_index: int | None = Field(
        None,
        title="Stream Index",
        description="Index of the stream within the media file",
    )
    codec_type: str = Field(
        ...,
        title="Codec Type",
        description="Type of codec (video, audio, subtitle, etc.)",
    )
    codec_name: str | None = Field(None, title="Codec Name", description="Name of the codec used")
    language: str | None = Field(None, title="Language", description="Language of the stream")
    width: int | None = Field(
        None, title="Width", description="Width in pixels (for video streams)"
    )
    height: int | None = Field(
        None, title="Height", description="Height in pixels (for video streams)"
    )
    frame_rate: float | None = Field(
        None, title="Frame Rate", description="Frame rate in fps (for video streams)"
    )
    channels: int | None = Field(
        None,
        title="Channels",
        description="Number of audio channels (for audio streams)",
    )
    sample_rate: int | None = Field(
        None, title="Sample Rate", description="Sample rate in Hz (for audio streams)"
    )
    is_default: bool | None = Field(
        None,
        title="Is Default",
        description="Whether this stream is the default stream",
    )
    is_forced: bool | None = Field(
        None, title="Is Forced", description="Whether this stream is forced"
    )
    title: str | None = Field(None, title="Title", description="Title or description of the stream")


class StreamCreatePublic(StreamAttrs):
    pass


class StreamCreateInternal(StreamCreatePublic):
    asset_id: int = Field(..., title="Asset ID", description="ID of the associated asset")


class StreamRead(StreamCreateInternal, IDMixin):
    pass


StreamPatchPublic = make_partial_model(StreamCreatePublic, name="StreamPatchPublic")

StreamUpdateInternal = make_partial_model(StreamCreateInternal, name="StreamUpdateInternal")
