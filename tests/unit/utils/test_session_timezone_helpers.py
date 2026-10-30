from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.schemas import TagRead
from app.utils.session_timezone import engine_connect_args, pinned_conninfo

pytestmark = pytest.mark.unit


def test_engine_connect_args_pin_utc_after_the_urls_options() -> None:
    bare = "postgresql+psycopg://u:p@h/db"
    assert engine_connect_args(bare) == {"options": "-c TimeZone=UTC"}
    one = f"{bare}?options=-csearch_path%3Ds"
    assert engine_connect_args(one) == {"options": "-csearch_path=s -c TimeZone=UTC"}
    two = f"{one}&options=-cstatement_timeout%3D5"
    assert engine_connect_args(two) == {
        "options": "-csearch_path=s -cstatement_timeout=5 -c TimeZone=UTC"
    }


def test_pinned_conninfo_keeps_the_dsns_own_options() -> None:
    bare = pinned_conninfo("postgresql://u:p@h:5432/db")
    assert conninfo_to_dict(bare) == {
        "user": "u",
        "password": "p",
        "host": "h",
        "port": "5432",
        "dbname": "db",
        "options": "-c TimeZone=UTC",
    }
    own = pinned_conninfo("host=h dbname=db options='-c statement_timeout=5'")
    assert conninfo_to_dict(own)["options"] == "-c statement_timeout=5 -c TimeZone=UTC"


def test_a_london_timestamp_read_from_a_row_goes_out_as_utc() -> None:
    # A row as psycopg hands it back on a London session: from_attributes input, which
    # the old model validator never saw.
    london = datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("Europe/London"))
    row = SimpleNamespace(
        id=1,
        name="t",
        description=None,
        color="#6B7280",
        parent_id=None,
        created_at=london,
        updated_at=london,
    )
    read = TagRead.model_validate(row)
    assert read.created_at.tzinfo is UTC
    assert read.model_dump(mode="json")["created_at"] == "2026-07-01T11:00:00Z"
    from_dict = TagRead.model_validate({**vars(row), "updated_at": "2026-07-01T12:00:00+01:00"})
    assert from_dict.updated_at.tzinfo is UTC
