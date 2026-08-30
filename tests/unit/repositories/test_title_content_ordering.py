"""The positioning arithmetic behind title contents ordering.

``target_index`` is the whole of it, and it is a pure function precisely so that these
cases need no database. The scheme it replaced (#128) could not be tested this way: its
key generator reached into the shape of neighbouring strings, so every interesting case
was a fixture of stored rows rather than a list of ids.
"""

import pytest

from app.repositories.title_content_repository import target_index


@pytest.mark.unit
class TestAnchors:
    def test_start_is_the_front(self):
        assert target_index([1, 2, 3], anchor="start") == 0

    def test_end_is_past_the_last(self):
        assert target_index([1, 2, 3], anchor="end") == 3

    def test_an_empty_list_places_at_zero_either_way(self):
        assert target_index([], anchor="start") == 0
        assert target_index([], anchor="end") == 0

    def test_an_anchor_outranks_a_neighbour(self):
        """`position=start` with a `before_id` is contradictory; start wins, as before."""
        assert target_index([1, 2, 3], before_id=3, anchor="start") == 0

    def test_an_unrecognised_anchor_appends(self):
        """The query parameter is free-form text, so nonsense has to mean something."""
        assert target_index([1, 2, 3], anchor="middle") == 3


@pytest.mark.unit
class TestNeighbours:
    def test_before_lands_on_the_neighbour_s_index(self):
        assert target_index([1, 2, 3], before_id=2) == 1

    def test_before_the_first_is_the_front(self):
        assert target_index([1, 2, 3], before_id=1) == 0

    def test_after_lands_past_the_neighbour(self):
        assert target_index([1, 2, 3], after_id=2) == 2

    def test_after_the_last_is_the_end(self):
        assert target_index([1, 2, 3], after_id=3) == 3

    def test_no_instruction_at_all_appends(self):
        assert target_index([1, 2, 3]) == 3


@pytest.mark.unit
class TestUnknownNeighbours:
    """A neighbour that is not in the list, which callers resolve differently.

    Returning None rather than guessing is what lets ``reorder`` leave an entry where it
    is while ``create_positioned`` appends -- the same input, two correct answers.
    """

    def test_an_unknown_before_id_is_undecidable(self):
        assert target_index([1, 2, 3], before_id=99) is None

    def test_an_unknown_after_id_is_undecidable(self):
        assert target_index([1, 2, 3], after_id=99) is None

    def test_any_neighbour_is_unknown_in_an_empty_list(self):
        assert target_index([], before_id=1) is None
