"""Read the pixel dimensions of a stored artwork file.

Pillow rather than hand-rolled header parsing. ``ArtworkStore`` admits JPEG, PNG, GIF,
WebP and AVIF, and of those only PNG and GIF carry their size at a fixed offset: JPEG
needs a walk over its segments to find an SOF marker, WebP has three incompatible chunk
layouts (VP8, VP8L, VP8X), and AVIF means parsing ISO-BMFF boxes down to ``ispe``. That
is a hundred lines of bit-twiddling whose failure mode is a plausible *wrong number*
rather than a loud error -- and a wrong number here becomes a layout the browse grid
reserves space against.

Pillow is an optional extra rather than a runtime dependency. Nothing the API serves
decodes images: the store identifies formats from magic bytes and never opens them. The
production image should not carry an image library for the sake of a maintenance pass
that runs once, so the import is deferred and its absence explained rather than raised
as a bare ``ModuleNotFoundError``.
"""

from __future__ import annotations

from pathlib import Path

#: What to tell an operator who ran the tool without the extra installed.
MISSING_PILLOW = (
    "Pillow is not installed. It is an optional extra, because nothing the API "
    "itself serves needs to decode an image.\n"
    "    uv sync --extra dimensions\n"
    "or, for a one-off invocation:\n"
    "    uv run --extra dimensions artwork-dimensions"
)


def pillow_missing() -> bool:
    """Whether the optional image dependency is absent.

    Checked once up front so the CLI can refuse with :data:`MISSING_PILLOW` before
    opening a database connection, rather than failing on the first file.

    Returns:
        bool: True if Pillow cannot be imported.
    """
    try:
        import PIL  # noqa: F401, PLC0415 - probing for an optional extra, see module docstring

        return False
    except ImportError:
        return True


def measure(path: Path) -> tuple[int, int]:
    """Read an image's pixel dimensions.

    Only the header is read: ``Image.open`` is lazy and ``size`` is populated from it,
    so this does not decode the pixels. That matters at a few thousand files.

    Args:
        path: The image to measure.

    Returns:
        tuple[int, int]: Width and height in pixels.

    Raises:
        OSError: If the file cannot be read, or Pillow cannot identify it as an image.
    """
    # Deferred because Pillow is an optional extra that must not be required at import
    # time -- the second of the two exceptions CLAUDE.md permits, and the same shape as
    # the orchestration providers' handling of prefect.
    from PIL import Image  # noqa: PLC0415

    with Image.open(path) as image:
        width, height = image.size
    return width, height
