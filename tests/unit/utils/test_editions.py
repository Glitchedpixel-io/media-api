"""Unit tests for the filename edition parser (issue #92).

The parser is guesswork over a naming convention, so what these assert is mostly where it
must *not* guess. Two properties carry the design:

An unrecognised but deliberate marker is preserved rather than dropped. Null on
`assets.edition` licenses a UI to choose between siblings silently, so folding an edition
this vocabulary does not know into null would tell it to pick silently between two
different cuts -- the exact failure the field exists to prevent.

A word that happens to appear in a title is not an edition. "Uncut Gems" is the canonical
example, and the reason inferred matches are separated from explicit ones in the report
rather than applied wholesale.
"""

from __future__ import annotations

import pytest

from app.utils.editions import CANONICAL_EDITIONS, EditionSource, parse_edition


@pytest.mark.unit
class TestExplicitMarkers:

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("Blade Runner (1982) {edition-Final Cut}.mkv", "final_cut"),
            ("Aliens (1986) {edition-Director's Cut}.mkv", "directors_cut"),
            ("Aliens (1986) {edition-Directors Cut}.mkv", "directors_cut"),
            ("Film {edition-Extended Edition}.mkv", "extended"),
            ("Film {edition-DC}.mkv", "directors_cut"),
        ],
    )
    def test_recognises_the_plex_convention(self, filename: str, expected: str):
        match = parse_edition(filename)

        assert match is not None
        assert match.value == expected
        assert match.source is EditionSource.explicit
        assert match.canonical is True

    def test_an_apostrophe_does_not_lose_the_match(self):
        """ "Director's Cut" must normalise to "directors cut", not "director s cut"."""
        assert parse_edition("F {edition-Director's Cut}.mkv").value == "directors_cut"

    def test_an_unknown_explicit_marker_is_preserved_not_dropped(self):
        """The property the free-text column exists for: null would mean 'pick silently'."""
        match = parse_edition("Dune (2021) {edition-IMAX Enhanced}.mkv")

        assert match is not None
        assert match.value == "imax_enhanced"
        assert match.canonical is False
        assert match.raw == "IMAX Enhanced"

    def test_explicit_beats_a_bracketed_aside(self):
        match = parse_edition("Film (Extended) {edition-Theatrical}.mkv")

        assert match is not None
        assert match.value == "theatrical"
        assert match.source is EditionSource.explicit


@pytest.mark.unit
class TestInferredMarkers:

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("Movie.2019.EXTENDED.1080p.BluRay.mkv", "extended"),
            ("Movie.2019.UNCUT.1080p.mkv", "uncut"),
            ("Movie.2019.REMASTERED.2160p.mkv", "remastered"),
            ("Movie (Extended Edition).mkv", "extended"),
            ("Movie [Unrated].mkv", "unrated"),
            ("Apocalypse Now (1979) Redux.mkv", "redux"),
        ],
    )
    def test_recognises_common_conventions(self, filename: str, expected: str):
        match = parse_edition(filename)

        assert match is not None
        assert match.value == expected
        assert match.source is EditionSource.inferred


@pytest.mark.unit
class TestWhatItRefusesToGuess:

    @pytest.mark.parametrize(
        "filename",
        [
            "Uncut Gems (2019).mkv",
            "The Godfather (1972).mkv",
            "Show S01E01.mkv",
            "Ordinary Film 12 (2001).mkv",
            "",
        ],
    )
    def test_returns_none_when_there_is_no_marker(self, filename: str):
        """None is not 'unknown'. It is the licence to choose silently between siblings,
        so a false positive here is worse than a miss."""
        assert parse_edition(filename) is None

    def test_ambiguous_short_forms_are_explicit_only(self):
        """`DC` is a city, a comics publisher and a director's cut. Only the deliberate
        marker gets to mean the third."""
        assert parse_edition("Film.2019.DC.1080p.mkv") is None
        assert parse_edition("Film {edition-DC}.mkv").value == "directors_cut"

    def test_the_extension_is_not_searched(self):
        assert parse_edition("Movie.extended") is None


@pytest.mark.unit
def test_every_synonym_maps_into_the_canonical_vocabulary():
    """A synonym pointing at a slug outside the vocabulary would be stored and never
    documented, which is how a vocabulary quietly stops being one."""
    from app.utils.editions import _SYNONYMS

    assert set(_SYNONYMS.values()) <= set(CANONICAL_EDITIONS)
