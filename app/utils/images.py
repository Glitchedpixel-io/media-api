# app/utils/images.py
"""Read the pixel dimensions of an image file, and refuse what cannot be read.

Pillow rather than hand-rolled header parsing. ``ArtworkStore`` admits JPEG, PNG, GIF,
WebP and AVIF, and of those only PNG and GIF carry their size at a fixed offset: JPEG
needs a walk over its segments to find an SOF marker, WebP has three incompatible chunk
layouts (VP8, VP8L, VP8X), and AVIF means parsing ISO-BMFF boxes down to ``ispe``. That
is a hundred lines of bit-twiddling whose failure mode is a plausible *wrong number*
rather than a loud error -- and a wrong number here becomes a layout a client reserves
space against.

Only the header is read. ``Image.open`` is lazy and ``size`` is populated from it, so
nothing here decodes pixels; ``load()`` is never called. That is what makes an image
library affordable on the request path rather than only in a maintenance pass.

The errors below subclass ``OSError`` because that is what Pillow raises for a file it
cannot identify, and callers that already handle "this file could not be measured" as
an ``OSError`` keep working unchanged.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image

#: Refuse an image declaring more pixels than this, however few bytes it arrived in.
#:
#: A decompression bomb is the case that matters: a 20KB PNG can declare 50000x50000 in
#: its IHDR, which measures honestly and produces a row no client can size a layout
#: from. ``MAX_ARTWORK_BYTES`` does not catch it, because the file is small on the wire
#: and only enormous once interpreted.
#:
#: 50 megapixels is comfortably clear of 8K (33MP) and roughly six times a 4K backdrop,
#: so nothing legitimate is near it. A constant rather than config, for the same reason
#: as ``MAX_ARTWORK_BYTES``: a knob nobody sets is a knob that drifts from reality.
MAX_IMAGE_PIXELS = 50_000_000


class ImageMeasurementError(OSError):
    """An image's dimensions could not be established."""


class UnreadableImage(ImageMeasurementError):
    """The bytes could not be interpreted as an image at all."""


class ImageTooManyPixels(ImageMeasurementError):
    """The image declares more pixels than :data:`MAX_IMAGE_PIXELS`."""


def measure(path: Path) -> tuple[int, int]:
    """Read an image's pixel dimensions.

    Args:
        path: The image to measure.

    Returns:
        tuple[int, int]: Width and height in pixels.

    Raises:
        UnreadableImage: If the file cannot be read, or Pillow cannot identify it.
        ImageTooManyPixels: If the image declares more than :data:`MAX_IMAGE_PIXELS`.
    """
    with warnings.catch_warnings():
        # Pillow warns above its own soft ceiling and raises above twice it. The
        # explicit check below is the one that decides, so the warning is noise --
        # suppressed inside this call only, never as global warning state.
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Image.DecompressionBombError as exc:
            # Pillow's own hard ceiling, hit before ours could be applied.
            raise ImageTooManyPixels(f"Image at {path} is too large to process") from exc
        except (OSError, ValueError) as exc:
            raise UnreadableImage(f"Image at {path} could not be read") from exc

    if width * height > MAX_IMAGE_PIXELS:
        raise ImageTooManyPixels(
            f"Image at {path} declares {width}x{height} pixels, over the "
            f"{MAX_IMAGE_PIXELS} limit"
        )
    return width, height
