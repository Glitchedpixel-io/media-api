# app/schemas/transcript_search.py

from pydantic import BaseModel, Field, field_validator

from app.utils.paths import to_linux_path


class TranscriptSearchQuery(BaseModel):
    q: str = Field(..., description="Query string (phrase)")
    mode: str = Field(
        default="exact",
        description="Search mode: exact or similar",
    )
    offset: int = Field(0, ge=0, description="Offset for pagination")
    size: int = Field(25, ge=1, le=200, description="Page size (max 200)")
    path_prefix: str | None = Field(None, description="Filter by media path prefix (e.g. /movies/)")
    path_part: str | None = Field(None, description="Filter by path substring (slower)")
    collection: str | None = Field(None, description="Filter by collection value")
    title_part: str | None = Field(None, description="Filter by title fragment (e.g. 'Star Wars')")
    asset_id: int | None = Field(None, description="Restrict to a single asset id")
    language: str | None = Field(None, description="Restrict by language code (normalized lower)")

    model_config = {"from_attributes": True}

    @field_validator("path_prefix", "path_part", mode="before")
    @classmethod
    def _normalize_paths(cls, v):  # type: ignore
        return to_linux_path(v) if v is not None else v

    @field_validator("collection", "title_part", "language", mode="before")
    @classmethod
    def _lowercase(cls, v):  # type: ignore
        return v.lower() if isinstance(v, str) else v


class TranscriptSearchHit(BaseModel):
    asset_id: int = Field(..., description="ID of the asset this transcript segment belongs to")
    segment_id: int = Field(..., description="ID of the matched transcript segment")
    start_s: float = Field(
        ..., description="Start time of the segment within the media, in seconds"
    )
    end_s: float = Field(..., description="End time of the segment within the media, in seconds")
    media_path: str | None = Field(
        None, description="Filesystem path of the asset the segment belongs to"
    )
    media_title: str | None = Field(None, description="Title of the asset the segment belongs to")
    language: str | None = Field(None, description="Language code of the transcript segment")
    text: str | None = Field(None, description="Full text of the matched segment")
    score: float | None = Field(None, description="Elasticsearch relevance score for this hit")
    highlight: list[str] = Field(
        default=[],
        description="Highlighted excerpt(s) matching the query, with matches wrapped in <em> tags",
    )

    model_config = {"from_attributes": True}


class TranscriptSearchResponse(BaseModel):
    items: list[TranscriptSearchHit] = Field(..., description="Matched transcript segments")
    total: int = Field(..., description="Total number of matching segments across all pages")
    model_config = {"from_attributes": True}
