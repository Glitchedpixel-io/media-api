# app/services/artwork_storage.py
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from fastapi import HTTPException

from app.config import MediaConfig
from app.utils.images import ImageTooManyPixels, UnreadableImage, measure
from app.utils.paths import artwork_relative_path

#: How much of an upload to read at a time. Small enough that a rejected upload is
#: abandoned early, large enough not to make syscalls the bottleneck.
_CHUNK_BYTES = 64 * 1024

#: Refuse an upload larger than this. An upload endpoint without a ceiling is a way to
#: fill the volume `media_root` shares, so this is a cap rather than a preference.
#: Sized for artwork: a 4K backdrop lands around 5MB, so 25MB is generous for every
#: kind the seed list carries. Deliberately a constant rather than config -- nothing
#: has asked to tune it, and a knob nobody sets is a knob that drifts from reality.
MAX_ARTWORK_BYTES = 25 * 1024 * 1024

#: Magic-number prefixes for the image formats artwork may be stored in, mapped to the
#: canonical (mime, suffix) pair this service records.
#:
#: The uploaded filename and the request's Content-Type are both attacker-controlled,
#: so neither decides what a file is: an HTML document named `poster.jpg` would
#: otherwise be stored and later served as an image. Sniffing the bytes is the only
#: claim here that the client cannot forge.
_SIGNATURES: tuple[tuple[bytes, int, str, str], ...] = (
    (b"\xff\xd8\xff", 0, "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", 0, "image/png", ".png"),
    (b"GIF87a", 0, "image/gif", ".gif"),
    (b"GIF89a", 0, "image/gif", ".gif"),
)

#: How many bytes the sniffer needs before it can decide. The longest fixed signature
#: is 8 bytes; the container formats below need 16.
_SNIFF_BYTES = 16

NOT_AN_IMAGE = "Artwork must be a JPEG, PNG, WebP, GIF or AVIF image"

#: A file whose signature says "image" but whose dimensions cannot be read -- truncated,
#: corrupt, or a format Pillow declines. Refused rather than stored: an image the API
#: knows nothing about is one it cannot responsibly serve (#140).
UNREADABLE_IMAGE = "Artwork image data could not be read"

TOO_MANY_PIXELS = "Artwork declares more pixels than this service will accept"


class _Sink(Protocol):
    """Whatever ``_consume`` copies bytes into.

    Narrower than ``BinaryIO`` on purpose: the only thing needed is ``write``, and
    ``tempfile.NamedTemporaryFile`` returns a wrapper that satisfies that without
    declaring itself a ``BinaryIO``.
    """

    def write(self, data: bytes, /) -> int: ...


@dataclass(frozen=True)
class StoredArtwork:
    """Where an uploaded file landed, and what it turned out to be."""

    digest: str
    suffix: str
    mime: str
    size: int
    width: int
    height: int
    storage_path: str
    already_present: bool


def _sniff(head: bytes) -> tuple[str, str] | None:
    """Identify an image from its leading bytes.

    Args:
        head: At least ``_SNIFF_BYTES`` bytes from the start of the file.

    Returns:
        tuple[str, str] | None: The canonical ``(mime, suffix)``, or None if these
            bytes are not an image format artwork may be stored in.
    """
    for signature, offset, mime, suffix in _SIGNATURES:
        if head[offset : offset + len(signature)] == signature:
            return mime, suffix

    # RIFF containers carry their format in a second tag rather than a prefix, so
    # matching "RIFF" alone would accept a WAV file as artwork.
    if head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"

    # ISO-BMFF: a length-prefixed "ftyp" box whose brand distinguishes AVIF from the
    # MP4 files this library is otherwise full of. "avis" is the sequence variant.
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "image/avif", ".avif"

    return None


class ArtworkStore:
    """Reads an upload, identifies it, and writes it into ARTWORK_ROOT.

    Deliberately knows nothing about artwork rows. Its whole contract is: given a
    stream of bytes, either produce a file on disk in the content-addressed layout and
    describe it, or refuse.
    """

    def __init__(self, config: MediaConfig) -> None:
        self.root = Path(config.artwork_root)

    def _consume(self, stream: BinaryIO, sink: _Sink | None) -> tuple[str, str, str, int]:
        """Read a stream once, identifying and digesting it as it goes.

        Shared by :meth:`store` and :meth:`inspect` so that the size cap, the format
        sniffing and the digest have exactly one implementation. A dry run that
        validated by a second, looser set of rules than the real write would report a
        scope the real run does not deliver.

        Args:
            stream: The bytes to read.
            sink: Where to copy them, or None to discard as they are consumed.

        Returns:
            tuple[str, str, str, int]: digest, mime, suffix, size.

        Raises:
            HTTPException: 400 if empty, 413 if over ``MAX_ARTWORK_BYTES``, 415 if the
                bytes are not an image format artwork may be stored in.
        """
        hasher = hashlib.sha256()
        size = 0
        head = b""
        sniffed: tuple[str, str] | None = None

        while chunk := stream.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_ARTWORK_BYTES:
                # Refuse mid-stream rather than after buffering the whole upload --
                # the point of a cap is not to hold the bytes.
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"Artwork exceeds the maximum size of "
                        f"{MAX_ARTWORK_BYTES // (1024 * 1024)}MB"
                    ),
                )
            if sniffed is None:
                head += chunk[: _SNIFF_BYTES - len(head)]
                if len(head) >= _SNIFF_BYTES:
                    sniffed = _sniff(head)
                    if sniffed is None:
                        raise HTTPException(status_code=415, detail=NOT_AN_IMAGE)
            hasher.update(chunk)
            if sink is not None:
                sink.write(chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="Artwork file is empty")
        if sniffed is None:
            # Shorter than _SNIFF_BYTES, so the loop never got enough to decide. Too
            # short to be any of these formats, which is the same refusal.
            raise HTTPException(status_code=415, detail=NOT_AN_IMAGE)

        mime, suffix = sniffed
        return hasher.hexdigest(), mime, suffix, size

    @staticmethod
    def _measure(path: Path) -> tuple[int, int]:
        """Read a staged file's dimensions, or refuse it.

        The backend is responsible for what it serves, so the dimensions are taken
        from the bytes rather than from anything the caller said about them: a
        client-supplied size is a claim about a file the client also controls, which
        is the same reasoning that makes the store sniff its own MIME type.

        Args:
            path: The complete file on disk.

        Returns:
            tuple[int, int]: Width and height in pixels.

        Raises:
            HTTPException: 400 if the image cannot be read, 413 if it declares more
                pixels than ``MAX_IMAGE_PIXELS``.
        """
        try:
            return measure(path)
        except ImageTooManyPixels as exc:
            raise HTTPException(status_code=413, detail=TOO_MANY_PIXELS) from exc
        except UnreadableImage as exc:
            raise HTTPException(status_code=400, detail=UNREADABLE_IMAGE) from exc

    def _stage(self, stream: BinaryIO) -> tuple[Path, StoredArtwork]:
        """Write a stream to a temporary file and describe what it turned out to be.

        Shared by :meth:`store` and :meth:`inspect` so the size cap, the format
        sniffing, the digest and the measurement have exactly one implementation and
        both methods accept and refuse precisely the same inputs. A dry run that
        validated by a second, looser set of rules than the real write would report a
        scope the real run does not deliver.

        Measuring needs the complete file with random access -- an SOF marker can sit
        well into a JPEG -- so this is also why the dry run now stages bytes rather
        than discarding them as it reads.

        The caller owns the temporary file on success: :meth:`store` moves it into
        place, :meth:`inspect` deletes it. On any refusal it is removed here, because
        a `.part` file left in the root is one nothing would ever collect.

        Args:
            stream: The bytes to examine.

        Returns:
            tuple[Path, StoredArtwork]: The staged file, and what storing it produces.

        Raises:
            HTTPException: 400 if the file is empty or unreadable as an image, 413 if
                it exceeds ``MAX_ARTWORK_BYTES`` or ``MAX_IMAGE_PIXELS``, or 415 if
                the bytes are not an image format artwork may be stored in.
        """
        self.root.mkdir(parents=True, exist_ok=True)

        # delete=False because the file outlives the context manager on the success
        # path, where the caller moves it rather than the cleanup removing it.
        tmp = tempfile.NamedTemporaryFile(dir=self.root, delete=False, suffix=".part")
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                digest, mime, suffix, size = self._consume(stream, tmp)

            width, height = self._measure(tmp_path)
            relative = artwork_relative_path(digest, suffix)
            return tmp_path, StoredArtwork(
                digest=digest,
                suffix=suffix,
                mime=mime,
                size=size,
                width=width,
                height=height,
                storage_path=relative,
                already_present=(self.root / relative).exists(),
            )
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def inspect(self, stream: BinaryIO) -> StoredArtwork:
        """Identify, measure and digest a stream without keeping anything.

        Answers "what would storing this produce, and would it be accepted?" -- which
        is what a dry run needs in order to report a scope the real run will actually
        deliver.

        It does touch the disk: measuring requires the complete file, so the bytes are
        staged under ``ARTWORK_ROOT`` and removed again before returning. Nothing
        survives the call.

        Args:
            stream: The bytes to examine.

        Returns:
            StoredArtwork: What :meth:`store` would produce. ``already_present``
                reports whether that file is on disk already; nothing is kept either
                way.

        Raises:
            HTTPException: The same refusals :meth:`store` raises.
        """
        tmp_path, described = self._stage(stream)
        tmp_path.unlink(missing_ok=True)
        return described

    def store(self, stream: BinaryIO) -> StoredArtwork:
        """Digest a stream and write it under ARTWORK_ROOT.

        The file is written to a temporary name first and moved into place with
        ``os.replace`` once the digest is known -- the final path is *derived* from
        the content, so it cannot be known before the last byte is read. The move is
        atomic within a filesystem, so a reader never observes a partial file at a
        content-addressed path. It is also what lets the measurement refuse a file
        before it ever appears at a content-addressed path.

        Writing is idempotent by construction: identical bytes always produce the same
        path, so storing a file already present is a no-op rather than a conflict.
        That is what makes "write the file, then insert the row" the safe order --
        unlike ``MediaService``'s rename, a repeated write destroys nothing, and a
        file left behind by a failed insert is inert rather than corrupting.

        Args:
            stream: The bytes to store.

        Returns:
            StoredArtwork: The digest, canonical type, measured size and relative path
                of the file.

        Raises:
            HTTPException: 400 if the file is empty or unreadable as an image, 413 if
                it exceeds ``MAX_ARTWORK_BYTES`` or ``MAX_IMAGE_PIXELS``, or 415 if
                the bytes are not an image format artwork may be stored in.
        """
        tmp_path, described = self._stage(stream)
        try:
            if described.already_present:
                # Same digest means same bytes, so the file on disk is already
                # correct. Replacing it would be a no-op that briefly unlinks a file
                # other artwork rows point at.
                tmp_path.unlink(missing_ok=True)
            else:
                target = self.root / described.storage_path
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return described
