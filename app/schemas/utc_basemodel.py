# app/schemas/utc_basemodel.py
from __future__ import annotations

from typing import Any, Union, get_args, get_origin, Annotated
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, model_validator

from app.utils.tz import ensure_utc

Timestamp = Annotated[datetime, AwareDatetime]


class UTCBaseModel(BaseModel):
    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def ensure_all_datetime_fields_utc(cls, data: Any) -> Any:
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
