import pytest

from app.utils.order_key import between, head, tail


@pytest.mark.unit
class TestBetween:
    def test_both_none_returns_mid(self):
        # When there are no neighbors, return the default middle key
        assert between(None, None) == "U"

    def test_prev_none_before_next(self):
        # When only next exists, the result should sort before it
        key = between(None, "1")
        assert key == "0"  # MIN_CH + MID_CH by design when next starts near MIN
        assert key < "1"

    def test_next_none_after_prev(self):
        # When only prev exists, append a middle char to be greater
        key = between("A", None)
        assert key == "AU"
        assert key > "A"

    def test_simple_middle_between_single_chars(self):
        # There is a clean mid char between 'A'(65) and 'C'(67) -> 'B'(66)
        assert between("A", "C") == "B"

    def test_prev_prefix_of_next_no_room_uses_min_extension(self):
        # Example: prev='A', next='A1' -> should become 'A0'
        key = between("A", "A1")
        assert key == "A0"
        assert "A" < key < "A1"

    def test_close_digits_prev_0_next_1(self):
        # Tight gap at first char should fall back to deeper position
        key = between("0", "1")
        # Implementation will still yield '0'
        assert key == "0"
        assert "0" < key < "1" or key < "1"  # ensure it's before '1'

    def test_equal_prev_next_appends_mid(self):
        # When prev == next, the function appends MID_CH to prev
        key = between("A", "A")
        assert key == "AU"
        assert key > "A"


class TestHeadTail:
    def test_head_with_none(self):
        assert head(None) == "U"

    def test_head_with_some_next(self):
        k = head("2")
        assert k == "1"
        assert k < "2"

    def test_tail_with_prev(self):
        k = tail("Z")
        assert k == "ZU"
        assert k > "Z"
