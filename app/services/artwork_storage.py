# app/services/artwork_storage.py
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException

from app.config import MediaConfig
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


@dataclass(frozen=True)
class StoredArtwork:
    """Where an uploaded file landed, and what it turned out to be."""

    digest: str
    suffix: str
    mime: str
    size: int
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

    def store(self, stream: BinaryIO) -> StoredArtwork:
        """Digest an uploaded stream and write it under ARTWORK_ROOT.

        The file is written to a temporary name first and moved into place with
        ``os.replace`` once the digest is known -- the final path is *derived* from
        the content, so it cannot be known before the last byte is read. The move is
        atomic within a filesystem, so a reader never observes a partial file at a
        content-addressed path.

        Writing is idempotent by construction: identical bytes always produce the same
        path, so re-uploading a file already present is a no-op rather than a
        conflict. That is what makes "write the file, then insert the row" the safe
        order -- unlike ``MediaService``'s rename, a repeated write destroys nothing,
        and a file left behind by a failed insert is inert rather than corrupting.

        Args:
            stream: The uploaded bytes.

        Returns:
            StoredArtwork: The digest, canonical type and relative path of the file.

        Raises:
            HTTPException: 400 if the upload is empty, 413 if it exceeds
                ``MAX_ARTWORK_BYTES``, or 415 if the bytes are not an image format
                artwork may be stored in.
        """
        self.root.mkdir(parents=True, exist_ok=True)

        hasher = hashlib.sha256()
        size = 0
        head = b""
        sniffed: tuple[str, str] | None = None

        # delete=False because the file outlives the context manager on the success
        # path, where os.replace moves it rather than the cleanup removing it.
        tmp = tempfile.NamedTemporaryFile(dir=self.root, delete=False, suffix=".part")
        tmp_path = Path(tmp.name)
        try:
            with tmp:
                while chunk := stream.read(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > MAX_ARTWORK_BYTES:
                        # Refuse mid-stream rather than after buffering the whole
                        # upload -- the point of a cap is not to hold the bytes.
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
                                raise HTTPException(
                                    status_code=415,
                                    detail=(
                                        "Artwork must be a JPEG, PNG, WebP, GIF or " "AVIF image"
                                    ),
                                )
                    hasher.update(chunk)
                    tmp.write(chunk)

            if size == 0:
                raise HTTPException(status_code=400, detail="Artwork file is empty")
            if sniffed is None:
                # Shorter than _SNIFF_BYTES, so the loop never got enough to decide.
                # Too short to be any of these formats, which is the same refusal.
                raise HTTPException(
                    status_code=415,
                    detail="Artwork must be a JPEG, PNG, WebP, GIF or AVIF image",
                )

            mime, suffix = sniffed
            digest = hasher.hexdigest()
            relative = artwork_relative_path(digest, suffix)
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)

            already_present = target.exists()
            if already_present:
                # Same digest means same bytes, so the file on disk is already
                # correct. Replacing it would be a no-op that briefly unlinks a file
                # other artwork rows point at.
                tmp_path.unlink(missing_ok=True)
            else:
                os.replace(tmp_path, target)

            return StoredArtwork(
                digest=digest,
                suffix=suffix,
                mime=mime,
                size=size,
                storage_path=relative,
                already_present=already_present,
            )
        except BaseException:
            # Any refusal above leaves a .part file behind otherwise, and those
            # accumulate in the root where nothing would ever collect them.
            tmp_path.unlink(missing_ok=True)
            raise
