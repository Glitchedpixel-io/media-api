# app/schemas/asset.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import Field, field_validator

from app.utils.paths import to_linux_path


from ._dynamic import make_partial_model
from .mixins import IDMixin
from .tag import TagRead
from .id_scheme import ExternalIdentifierRead
from .utc_basemodel import UTCBaseModel, Timestamp


class AssetAttrs(UTCBaseModel):
    model_config = {"from_attributes": True, "extra": "forbid"}

    path: str = Field(..., title="Asset path", description="Asset path")
    filename: str = Field(..., title="Filename", description="Leafname of the asset")
    duration: float = Field(..., title="Duration", description="Duration of the asset in seconds")
    bitrate: int = Field(..., title="Bitrate", description="Bitrate of the asset in bps")
    container_format: str | None = Field(
        None,
        title="Container format",
        description="Container format of the asset file, may include multiple identifiers",
    )
    edition: str | None = Field(
        None,
        title="Edition",
        description=(
            "Which cut of the work this file is, as a slug: `theatrical`, "
            "`directors_cut`, `extended`. Null means no edition is recorded, which a "
            "client may read as 'safe to choose between siblings silently' -- siblings "
            "differing only in encoding. A non-null value that differs between siblings "
            "means they are different content and the choice belongs to the person. "
            "Values outside the canonical vocabulary are possible and deliberate: an "
            "unrecognised edition is still an edition"
        ),
    )
    size: int = Field(..., title="Size", description="Size of the asset in bytes")
    mtime: Timestamp | None = Field(None, description="Timestamp of the last modification")
    last_seen: Timestamp | None = Field(
        None, description="Timestamp when the asset was last known to exist in storage"
    )

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, v):  # type: ignore
        return to_linux_path(v)

    @property
    def time_since_last_seen(self) -> timedelta:
        """
        Computes the time since the entity was last seen.

        This property calculates the time difference between the current UTC
        time and the timestamp of the last recorded observation (`last_seen`).
        If `last_seen` is not set, it will return the maximum possible
        time duration (`timedelta.max`).

        :return: The time difference as a `timedelta` object. If the `last_seen`
            attribute is `None`, returns `timedelta.max`.
        :rtype: timedelta
        """
        if self.last_seen is None:
            return timedelta.max
        # Type checker knows self.last_seen is datetime here
        return datetime.now(UTC) - self.last_seen


class AssetCreatePublic(AssetAttrs):
    pass


class AssetCreateInternal(AssetAttrs):
    master_asset_id: int | None = Field(
        None, title="Master Asset ID", description="ID of the master asset"
    )


class AssetRead(AssetCreateInternal, IDMixin):
    created_at: Timestamp = Field(..., description="When the record was created")


class AssetReadExtended(AssetRead):
    master_asset: AssetRead | None = Field(
        None,
        title="Master Asset",
        description="Master asset from which this one was derived",
    )
    tags: list[TagRead] | None = Field(
        None, title="List of tags", description="Tags applied to this asset"
    )
    external_ids: list[ExternalIdentifierRead] | None = Field(
        None,
        title="List of external identifiers for this asset",
        description="External identifiers (e.g. from configured ID schemes) associated with this asset",
    )


AssetPatchPublic = make_partial_model(AssetCreatePublic, name="AssetPatchPublic")


AssetUpdateInternal = make_partial_model(AssetCreateInternal, name="AssetUpdateInternal")


class AssetSeenBatch(UTCBaseModel):
    model_config = {"extra": "forbid"}
    ids: list[int] = Field(..., min_length=1, description="List of asset IDs to mark as seen")
