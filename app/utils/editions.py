# app/utils/editions.py
"""Recognise an edition marker in a filename.

An *edition* is a different cut of the same work -- theatrical against director's cut --
as opposed to a different encoding of the same cut, which is what resolution and codec
describe. The distinction decides whether a detail screen plays or asks (#92): sibling
assets that differ only in encoding can be selected between silently, while siblings that
differ in edition must be offered to the person.

This module is an **ingest-time tool and never a runtime dependency**. Nothing in a
request path calls it. It exists to turn a filename convention into a stored field once,
and to keep doing so for newly scanned files; a detail screen reads `assets.edition` and
does no string matching of its own. That is the entire point of #92 -- matching at render
time would put this parser's guesswork on the critical path of every page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: The canonical vocabulary. Stored values are slugs so they are stable under display
#: changes, and so the API field does not carry apostrophes and capitalisation.
#:
#: Deliberately *not* a Postgres enum, and not a closed set at the database. See
#: ``parse_edition`` for why a marker outside this vocabulary must be preserved rather
#: than folded to NULL.
CANONICAL_EDITIONS: tuple[str, ...] = (
    "theatrical",
    "directors_cut",
    "extended",
    "unrated",
    "uncut",
    "remastered",
    "special_edition",
    "final_cut",
    "ultimate_cut",
    "anniversary",
    "redux",
)

#: Spellings seen in the wild, mapped to the canonical slug. Keys are matched
#: case-insensitively against a normalised form with punctuation stripped, so
#: "Director's Cut", "directors cut" and "DIRECTORS.CUT" all arrive here as
#: "directors cut".
_SYNONYMS: dict[str, str] = {
    "theatrical": "theatrical",
    "theatrical cut": "theatrical",
    "theatrical edition": "theatrical",
    "directors cut": "directors_cut",
    "director cut": "directors_cut",
    "dc": "directors_cut",
    "extended": "extended",
    "extended cut": "extended",
    "extended edition": "extended",
    "extended version": "extended",
    "unrated": "unrated",
    "unrated cut": "unrated",
    "unrated edition": "unrated",
    "uncut": "uncut",
    "remastered": "remastered",
    "remaster": "remastered",
    "special edition": "special_edition",
    "se": "special_edition",
    "final cut": "final_cut",
    "ultimate cut": "ultimate_cut",
    "ultimate edition": "ultimate_cut",
    "anniversary": "anniversary",
    "anniversary edition": "anniversary",
    "redux": "redux",
}

#: Synonyms short enough to be ambiguous as bare scene tokens. "DC" is a city, a comics
#: publisher and a director's cut; "SE" is a special edition and a Swedish country code.
#: They are honoured only inside an explicit `{edition-...}` marker, never inferred from
#: a dotted token.
_EXPLICIT_ONLY: frozenset[str] = frozenset({"dc", "se"})

#: The Plex and Jellyfin convention: `Film (2019) {edition-Director's Cut}.mkv`. The only
#: unambiguous signal, because someone wrote it deliberately for this purpose.
_EXPLICIT = re.compile(r"\{edition-(?P<value>[^}]+)\}", re.IGNORECASE)

#: A bracketed or parenthesised aside: `Film (Extended Edition).mkv`, `Film [Unrated].mkv`.
_BRACKETED = re.compile(r"[(\[]\s*(?P<value>[^)\]]{2,40}?)\s*[)\]]")

#: A scene-style dotted or underscored token run: `Film.2019.EXTENDED.1080p.mkv`.
_TOKEN_RUN = re.compile(r"(?:^|[._\s-])(?P<value>[A-Za-z]+(?:[._\s-][A-Za-z]+)?)(?=[._\s-]|$)")

_PUNCT = re.compile(r"[^a-z0-9]+")

#: Removed outright rather than treated as a separator: "Director's Cut" must normalise
#: to "directors cut", not "director s cut", or the apostrophe alone drops it out of the
#: vocabulary and it gets stored as an unrecognised marker.
_APOSTROPHE = re.compile(r"['\u2019\u02bc]")


class EditionSource(str, Enum):
    """How confident the parse is, which is what makes a human review tractable.

    ``explicit`` markers were written to name an edition and can be accepted in bulk.
    ``inferred`` ones were recognised from a convention that also produces false
    positives -- a film genuinely called "Uncut Gems" is the obvious case -- and are the
    rows a reviewer actually has to read.
    """

    explicit = "explicit"
    inferred = "inferred"


@dataclass(frozen=True)
class EditionMatch:
    """What a filename yielded.

    Attributes:
        value: The slug to store. Canonical when the marker was recognised, otherwise a
            slug of the raw marker text.
        raw: The marker exactly as it appeared, for a reviewer to judge.
        source: Whether the marker was explicit or inferred.
        canonical: Whether ``value`` is in :data:`CANONICAL_EDITIONS`.
    """

    value: str
    raw: str
    source: EditionSource
    canonical: bool


def _normalise(text: str) -> str:
    """Reduce a marker to lowercase words separated by single spaces."""
    return _PUNCT.sub(" ", _APOSTROPHE.sub("", text.lower())).strip()


def _slugify(text: str) -> str:
    """Turn arbitrary marker text into a storable slug."""
    return _PUNCT.sub("_", _APOSTROPHE.sub("", text.lower())).strip("_")


def _match(raw: str, source: EditionSource, *, allow_short: bool) -> EditionMatch | None:
    """Resolve one candidate marker against the vocabulary.

    Args:
        raw: The marker text as it appeared in the filename.
        source: Whether it came from an explicit marker or was inferred.
        allow_short: Whether the ambiguous short synonyms are eligible.

    Returns:
        EditionMatch | None: The match, or None if the text names no edition.
    """
    key = _normalise(raw)
    if not key:
        return None
    if key in _EXPLICIT_ONLY and not allow_short:
        return None
    slug = _SYNONYMS.get(key)
    if slug is not None:
        return EditionMatch(value=slug, raw=raw, source=source, canonical=True)
    if source is EditionSource.explicit:
        # Written deliberately as an edition, so it is one even though this vocabulary
        # does not name it. Preserving it is the whole reason the column is free text --
        # see parse_edition.
        return EditionMatch(value=_slugify(raw), raw=raw, source=source, canonical=False)
    return None


def parse_edition(filename: str) -> EditionMatch | None:
    """Find the edition marker in a filename, if it has one.

    Returns None when the filename carries no marker at all. That absence is meaningful
    and is what a NULL ``assets.edition`` records: nothing distinguishes this file's cut,
    so a UI may select it silently against its siblings.

    **An unrecognised marker is never folded into that None.** A file explicitly labelled
    with an edition this vocabulary does not know is still a distinct cut, and returning
    None for it would tell the UI it is safe to select silently -- the precise wrong
    answer, and the failure the field exists to prevent. Such a marker is returned with
    ``canonical=False`` so it is stored, and reported so the vocabulary can grow.

    Matching runs explicit-first and stops at the first hit, so a deliberate
    `{edition-...}` always beats a bracketed aside that happens to look like one.

    Args:
        filename: The leafname of the asset, with or without its extension.

    Returns:
        EditionMatch | None: The marker found, or None if there is none.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename

    explicit = _EXPLICIT.search(filename)
    if explicit:
        found = _match(explicit.group("value"), EditionSource.explicit, allow_short=True)
        if found is not None:
            return found

    for bracketed in _BRACKETED.finditer(stem):
        found = _match(bracketed.group("value"), EditionSource.inferred, allow_short=False)
        if found is not None:
            return found

    for token in _TOKEN_RUN.finditer(stem):
        found = _match(token.group("value"), EditionSource.inferred, allow_short=False)
        if found is not None:
            return found

    return None
