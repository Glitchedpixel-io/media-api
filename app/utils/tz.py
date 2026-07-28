# app/utils/tz.py
from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(v: datetime) -> datetime | None:
    if v is None:
        return v
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    # If naive, assume UTC
    if v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    # If aware, convert to UTC
    return v.astimezone(UTC)
