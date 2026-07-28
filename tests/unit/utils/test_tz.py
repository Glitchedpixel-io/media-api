# tests/unit/utils/test_tz.py
import pytest
from datetime import UTC, datetime, timezone, timedelta

from app.utils.tz import ensure_utc


@pytest.mark.unit
class TestEnsureUtc:
    def test_none_returns_none(self):
        assert ensure_utc(None) is None

    def test_naive_datetime_gets_utc_tzinfo(self):
        naive = datetime(2023, 6, 15, 10, 30, 0)
        result = ensure_utc(naive)
        assert result.tzinfo is UTC
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_utc_aware_datetime_unchanged(self):
        aware = datetime(2023, 6, 15, 10, 30, 0, tzinfo=UTC)
        result = ensure_utc(aware)
        assert result.tzinfo is UTC
        assert result == aware

    def test_non_utc_aware_datetime_converted_to_utc(self):
        # Create datetime in EST (UTC-5)
        est = timezone(timedelta(hours=-5))
        dt_est = datetime(2023, 6, 15, 10, 30, 0, tzinfo=est)
        result = ensure_utc(dt_est)
        # Should be converted to UTC
        assert result.tzinfo is UTC
        assert result.hour == 15  # 10 + 5 = 15

    def test_iso_string_parsed_and_converted(self):
        iso_string = "2023-06-15T10:30:00"
        result = ensure_utc(iso_string)
        assert result.tzinfo is UTC
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 15

    def test_iso_string_with_timezone_converted_to_utc(self):
        iso_string = "2023-06-15T10:30:00-05:00"
        result = ensure_utc(iso_string)
        assert result.tzinfo is UTC
        assert result.hour == 15  # 10 + 5 = 15

    def test_iso_string_with_utc_timezone(self):
        iso_string = "2023-06-15T10:30:00+00:00"
        result = ensure_utc(iso_string)
        assert result.tzinfo is UTC
        assert result.hour == 10

    def test_preserves_microseconds(self):
        naive = datetime(2023, 6, 15, 10, 30, 0, 123456)
        result = ensure_utc(naive)
        assert result.microsecond == 123456

    def test_different_timezone_conversion_positive_offset(self):
        # Create datetime in JST (UTC+9)
        jst = timezone(timedelta(hours=9))
        dt_jst = datetime(2023, 6, 15, 18, 0, 0, tzinfo=jst)
        result = ensure_utc(dt_jst)
        # Should be converted to UTC
        assert result.tzinfo is UTC
        assert result.hour == 9  # 18 - 9 = 9
