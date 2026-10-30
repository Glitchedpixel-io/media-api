# app/schemas/utc_basemodel.py
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Union, get_args, get_origin

from pydantic import AfterValidator, AwareDatetime, BaseModel, model_validator

from app.utils.tz import ensure_utc


def _to_utc(value: datetime) -> datetime:
    """Express a timestamp in UTC.

    Args:
        value (datetime): The validated timestamp, already checked to be aware.

    Returns:
        datetime: The same instant, zoned UTC.
    """
    return value.astimezone(UTC)


# The conversion to UTC sits on the type, so it runs however the model is built: from a
# dict, from JSON, or from an ORM row (`from_attributes`), and inside `Timestamp | None`.
# The model validator below only sees dict input, and matches a field by
# `annotation is Timestamp`, which never holds for a required field because pydantic
# unwraps `Annotated` -- so on its own a London-zoned value read from the database went
# out as `+01:00` (sentinel-core#525, D-239).
Timestamp = Annotated[datetime, AwareDatetime, AfterValidator(_to_utc)]


class UTCBaseModel(BaseModel):
    """A schema whose `Timestamp` fields always hold, and serialise as, UTC."""

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def ensure_all_datetime_fields_utc(cls, data: Any) -> Any:
        """Take a naive value given for an optional `Timestamp` to be UTC.

        Only optional fields match (see the note on `Timestamp`); a required field
        given a naive value is still refused as not timezone-aware.
        """
        if not isinstance(data, dict):
            return data

        for field_name, field_info in cls.model_fields.items():
            if field_name not in data or data[field_name] is None:
                continue

            annotation = field_info.annotation
            if annotation is AwareDatetime or annotation is Timestamp:
                data[field_name] = ensure_utc(data[field_name])
            elif get_origin(annotation) is Union:
                if AwareDatetime in get_args(annotation) or Timestamp in get_args(annotation):
                    data[field_name] = ensure_utc(data[field_name])

        return data
