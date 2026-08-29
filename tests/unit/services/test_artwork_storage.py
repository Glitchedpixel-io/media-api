# tests/unit/services/test_artwork_storage.py
"""Unit coverage for ArtworkStore.

Everything here is about not trusting the caller. The filename, the declared content
type and the declared size are all things the client controls; the bytes are the only
evidence. These tests exist so that stays true.
"""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image
from fastapi import HTTPException

from app.config.schema import MediaConfig
from app.services.artwork_storage import MAX_ARTWORK_BYTES, ArtworkStore
from app.utils.images import MAX_IMAGE_PIXELS


def _image_bytes(width: int = 40, height: int = 30, fmt: str = "JPEG") -> bytes:
    """A real, decodable image of a known size.

    Plausible headers are no longer enough to get through the store: since #140 it
    measures what it is given and refuses what it cannot read, so these fixtures have
    to be genuine images rather than magic numbers with padding.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format=fmt)
    return buffer.getvalue()


def _png_declaring(width: int, height: int) -> bytes:
    """A structurally valid PNG whose IHDR claims a size it does not contain.

    A decompression bomb in the shape that matters: a couple of hundred bytes on the
    wire, enormous once interpreted, so ``MAX_ARTWORK_BYTES`` cannot see it coming.
    The CRC is recomputed because Pillow verifies it on critical chunks.
    """
    patched = bytearray(_image_bytes(1, 1, "PNG"))
    # 8-byte signature, 4-byte length, 4-byte "IHDR", then width and height.
    patched[16:24] = struct.pack(">II", width, height)
    patched[29:33] = struct.pack(">I", zlib.crc32(bytes(patched[12:29])))
    return bytes(patched)


JPEG = _image_bytes(fmt="JPEG")
PNG = _image_bytes(fmt="PNG")
GIF = _image_bytes(fmt="GIF")
WEBP = _image_bytes(fmt="WEBP")
AVIF = _image_bytes(fmt="AVIF")


@pytest.fixture
def store(tmp_path: Path) -> ArtworkStore:
    return ArtworkStore(
        MediaConfig(
            media_root=str(tmp_path),
            accessory_root=str(tmp_path),
            inbox_root=str(tmp_path),
            artwork_root=str(tmp_path / "artwork"),
        )
    )


@pytest.fixture
def root(store: ArtworkStore) -> Path:
    return store.root


@pytest.mark.unit
class TestFormatSniffing:
    """The format comes from the bytes, never from what the caller claims."""

    @pytest.mark.parametrize(
        ("payload", "mime", "suffix"),
        [
            (JPEG, "image/jpeg", ".jpg"),
            (PNG, "image/png", ".png"),
            (GIF, "image/gif", ".gif"),
            (WEBP, "image/webp", ".webp"),
            (AVIF, "image/avif", ".avif"),
        ],
    )
    def test_recognises_each_supported_format(self, store, payload, mime, suffix):
        stored = store.store(io.BytesIO(payload))
        assert (stored.mime, stored.suffix) == (mime, suffix)

    def test_an_html_document_named_like_an_image_is_refused(self, store, root):
        """The attack the sniffing exists for: a file the caller calls poster.jpg,
        which would later be served to a browser as an image."""
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(b"<!doctype html><script>alert(1)</script>" + b" " * 32))
        assert exc.value.status_code == 415
        assert list(root.rglob("*")) == []

    def test_a_riff_container_that_is_not_webp_is_refused(self, store):
        """RIFF alone is a container marker -- a WAV file starts the same way, so
        matching the prefix without the WEBP tag would accept audio as artwork."""
        wav = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"fmt " + b"\x00" * 32
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(wav))
        assert exc.value.status_code == 415

    def test_an_mp4_is_refused_despite_its_ftyp_box(self, store):
        """This library is full of MP4s, which share AVIF's ISO-BMFF framing and
        differ only in the brand."""
        mp4 = b"\x00\x00\x00\x20" + b"ftyp" + b"isom" + b"\x00" * 32
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(mp4))
        assert exc.value.status_code == 415

    def test_a_file_too_short_to_identify_is_refused(self, store):
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(b"\xff\xd8\xff"))
        assert exc.value.status_code == 415

    def test_an_empty_file_is_400_not_415(self, store):
        """A distinct cause deserves a distinct status -- see the CLAUDE.md note on
        QuietClientErrorRoute needing the right code to begin with."""
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(b""))
        assert exc.value.status_code == 400


@pytest.mark.unit
class TestSizeCap:

    def test_an_oversized_upload_is_413(self, store):
        payload = JPEG + b"\x00" * (MAX_ARTWORK_BYTES + 1)
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(payload))
        assert exc.value.status_code == 413

    def test_an_oversized_upload_leaves_nothing_behind(self, store, root):
        """Including the temporary file. A refused upload that leaves a .part behind
        in the root is a slow leak nothing collects."""
        with pytest.raises(HTTPException):
            store.store(io.BytesIO(JPEG + b"\x00" * (MAX_ARTWORK_BYTES + 1)))
        assert list(root.rglob("*")) == []


@pytest.mark.unit
class TestContentAddressing:

    def test_the_path_is_derived_from_the_digest(self, store, root):
        stored = store.store(io.BytesIO(JPEG))
        expected = hashlib.sha256(JPEG).hexdigest()
        assert stored.digest == expected
        assert stored.storage_path == f"{expected[:2]}/{expected[2:4]}/{expected}.jpg"
        assert (root / stored.storage_path).read_bytes() == JPEG

    def test_storing_the_same_bytes_twice_is_idempotent(self, store, root):
        """What makes "write the file, then insert the row" safe: a repeat write
        destroys nothing, unlike the rename in MediaService."""
        first = store.store(io.BytesIO(PNG))
        second = store.store(io.BytesIO(PNG))

        assert first.storage_path == second.storage_path
        assert first.already_present is False
        assert second.already_present is True
        assert len(list(root.rglob("*.png"))) == 1

    def test_different_bytes_land_in_different_places(self, store):
        assert (
            store.store(io.BytesIO(JPEG)).storage_path != store.store(io.BytesIO(PNG)).storage_path
        )

    def test_no_partial_file_is_left_at_the_final_path(self, store, root):
        """The write goes to a temporary name and is moved into place, so a reader
        never sees a half-written file at a content-addressed path."""
        store.store(io.BytesIO(JPEG))
        assert list(root.rglob("*.part")) == []

    def test_the_root_is_created_on_demand(self, tmp_path):
        """A fresh deployment has no artwork_root until the first upload."""
        missing = tmp_path / "not-yet"
        store = ArtworkStore(
            MediaConfig(
                media_root=str(tmp_path),
                accessory_root=str(tmp_path),
                inbox_root=str(tmp_path),
                artwork_root=str(missing),
            )
        )
        store.store(io.BytesIO(JPEG))
        assert missing.is_dir()

    def test_the_reported_size_is_the_bytes_actually_read(self, store):
        stored = store.store(io.BytesIO(PNG))
        assert stored.size == len(PNG)


@pytest.mark.unit
class TestMeasurement:
    """The dimensions come from the bytes.

    A caller's claim about a file the caller also controls is not evidence -- the same
    reasoning that already makes the store sniff its own MIME type rather than trust
    the declared one. An image the API knows nothing about is one it cannot
    responsibly serve, so unmeasurable is a refusal, not a null column (#140).
    """

    def test_the_dimensions_are_read_from_the_image(self, store):
        stored = store.store(io.BytesIO(_image_bytes(321, 123)))
        assert (stored.width, stored.height) == (321, 123)

    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "GIF", "WEBP", "AVIF"])
    def test_every_admitted_format_can_be_measured(self, store, fmt):
        """A format the store admits but cannot measure would sniff as valid and then
        be refused as unreadable -- which is why the Pillow floor in pyproject.toml is
        11.3, where native AVIF landed."""
        stored = store.store(io.BytesIO(_image_bytes(64, 48, fmt)))
        assert (stored.width, stored.height) == (64, 48)

    def test_a_corrupt_image_is_refused(self, store):
        """Sniffs as a PNG, is not one."""
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(PNG[:20] + b"\x00" * 64))
        assert exc.value.status_code == 400

    def test_an_unreadable_image_leaves_nothing_behind(self, store, root):
        """Including the temporary file -- the refusal happens after the bytes are
        staged, so this is the case most likely to leak a .part."""
        with pytest.raises(HTTPException):
            store.store(io.BytesIO(PNG[:20] + b"\x00" * 64))
        assert list(root.rglob("*")) == []

    def test_a_decompression_bomb_is_refused(self, store):
        """Small on the wire, enormous once interpreted, so the byte cap cannot see
        it. Measured honestly it would be a valid row no client could lay out."""
        bomb = _png_declaring(10_000, 6_000)
        assert len(bomb) < 1024
        with pytest.raises(HTTPException) as exc:
            store.store(io.BytesIO(bomb))
        assert exc.value.status_code == 413

    def test_a_bomb_leaves_nothing_behind(self, store, root):
        with pytest.raises(HTTPException):
            store.store(io.BytesIO(_png_declaring(10_000, 6_000)))
        assert list(root.rglob("*")) == []

    def test_a_large_but_sane_image_is_accepted(self, store):
        """The ceiling has to clear real artwork: a 4K backdrop is about 8MP and 8K
        about 33MP, both well inside it."""
        stored = store.store(io.BytesIO(_png_declaring(7_000, 7_000)))
        assert stored.width * stored.height < MAX_IMAGE_PIXELS


@pytest.mark.unit
class TestInspectMatchesStore:
    """The dry run must not validate by a looser set of rules than the real write, or
    it reports a scope the real run does not deliver."""

    @pytest.mark.parametrize(
        "payload",
        [
            b"<!doctype html>" + b" " * 32,
            b"\xff\xd8\xff",
            b"",
            PNG[:20] + b"\x00" * 64,
            _png_declaring(10_000, 6_000),
        ],
        ids=["not-an-image", "too-short", "empty", "unreadable", "bomb"],
    )
    def test_inspect_refuses_whatever_store_refuses(self, store, payload):
        with pytest.raises(HTTPException) as inspected:
            store.inspect(io.BytesIO(payload))
        with pytest.raises(HTTPException) as written:
            store.store(io.BytesIO(payload))
        assert inspected.value.status_code == written.value.status_code

    def test_inspect_reports_what_store_would_produce(self, store):
        """Including the measured dimensions: a dry run that could not answer those
        would be reporting on a different set of rules than the write applies."""
        payload = _image_bytes(321, 123, "PNG")
        inspected = store.inspect(io.BytesIO(payload))
        assert inspected == store.store(io.BytesIO(payload))
        assert (inspected.width, inspected.height) == (321, 123)

    def test_inspect_keeps_nothing_on_disk(self, store, root):
        store.inspect(io.BytesIO(_image_bytes(321, 123, "PNG")))
        assert list(root.rglob("*")) == []
