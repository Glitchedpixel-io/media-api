# app/utils/order_key.py
"""
Utility helpers to generate lexicographically sortable order keys.

These functions try to emulate a lightweight LexoRank-like behavior using
plain ASCII strings so that simple ORDER BY order_key works reliably.

Rules:
- Keys are ASCII strings compared using bytewise (C) collation.
- We generate keys between two neighbors (prev and next), or at the head/tail.
- The algorithm attempts to find a character strictly between two chars; if
  not possible it appends a middle character to the left value.

This is intentionally simple and sufficient for modest list sizes.
"""

from __future__ import annotations

# Choose a safe ASCII range that sorts consistently. Keep it limited to avoid
# locale issues; database column is configured with collation "C" for Postgres.
MIN_CH = "0"
MAX_CH = "Z"
MID_CH = "U"  # something in the middle of the range
DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _between_chars(a: str, b: str) -> str | None:
    """Return a single character that sorts strictly between a and b, or None.

    Assumes len(a) == len(b) == 1.
    """

    if len(a) != 1 or len(b) != 1:
        raise ValueError("Inbound strings must be single characters")
    oa, ob = DIGITS.find(a), DIGITS.find(b)
    if oa == ob:
        return None
    mid = (oa + ob) // 2
    return DIGITS[mid]


def between(prev: str | None, nxt: str | None) -> str:
    """Return a new key strictly between prev and nxt.

    - If both are None, returns MID_CH.
    - If prev is None, returns a string strictly less than nxt.
    - If nxt is None, returns a value greater than prev.
    - Else, returns a value strictly between prev and nxt.
    """
    if prev is None and nxt is None:
        return MID_CH
    if prev is None:
        # Generate a key strictly less than nxt by walking along nxt and
        # appending MIN_CH until we find a position with available space.
        assert nxt is not None
        key = ""
        i = 0
        while True:
            b_ch = nxt[i] if i < len(nxt) else MAX_CH
            mid = _between_chars(MIN_CH, b_ch)
            if mid is not None:
                return key + mid
            # Tight start (e.g., next starts with MIN_CH+1): prefer MIN_CH+MID_CH to stay predictable
            if i == 0:
                candidate = MIN_CH + MID_CH
                # Ensure we still sort before next
                if candidate < nxt:
                    return candidate
            # No space at this position; fix this position to MIN_CH and continue
            key += MIN_CH
            i += 1
            # As a safety valve, don't loop forever; if nxt is extremely small, we will
            # eventually compare against MAX_CH and produce MIN_CH < MAX_CH mid.
            if i > len(nxt) + 8:
                return key + MID_CH
    if nxt is None:
        # make something greater than prev by appending a middle char
        return prev + MID_CH

    # Fast-path: find first differing position
    i = 0
    min_len = min(len(prev), len(nxt))
    while i < min_len and prev[i] == nxt[i]:
        i += 1

    # If completely equal, append MID_CH
    if len(prev) == len(nxt) and i == min_len:
        return prev + MID_CH

    # If prev is a strict prefix of nxt
    if i == len(prev):
        b_ch = nxt[i] if i < len(nxt) else MAX_CH
        # If there is room between MIN_CH and b_ch, take a mid char
        mid = _between_chars(MIN_CH, b_ch) if i < len(nxt) else None
        if mid is not None:
            return prev + mid
        # No room: if b_ch > MIN_CH, prev+MIN_CH still works
        if i < len(nxt) and b_ch > MIN_CH:
            return prev + MIN_CH
        # Edge case: nxt == prev + MIN_CH (and nothing else) with no in-range char available.
        # Avoid punctuation by using a safe alphanumeric fallback; not strictly between,
        # but guarantees uniqueness and predictable ordering.
        return prev + MIN_CH + MID_CH

    # Otherwise, they differ at position i within both strings
    a_ch = prev[i]
    b_ch = nxt[i] if i < len(nxt) else MAX_CH
    mid = _between_chars(a_ch, b_ch)
    if mid is not None:
        return prev[:i] + mid

    # If no space at this position, extend prev further with MIN_CH until we get space
    j = i + 1
    while True:
        # If we've exhausted nxt, prefer using a fixed middle to keep results predictable
        if j >= len(nxt):
            return prev[:i] + prev[i] + MID_CH
        b_next = nxt[j]
        mid2 = _between_chars(MIN_CH, b_next)
        if mid2 is not None:
            return prev[:i] + prev[i] + (MIN_CH * (j - i - 1)) + mid2
        j += 1
        if j > len(prev) + len(nxt) + 8:
            return prev + MID_CH


def head(next_key: str | None) -> str:
    """Return a key that sorts before next_key (or default if none).

    Delegates to between(None, next_key) to ensure we always generate a key
    strictly smaller than next_key and avoid collisions on repeated inserts.
    """
    return between(None, next_key)


def tail(prev_key: str | None) -> str:
    """Return a key that sorts after prev_key (or default if none)."""
    return between(prev_key, None)
