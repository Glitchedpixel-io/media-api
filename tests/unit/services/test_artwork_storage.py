# tests/unit/services/test_artwork_storage.py
"""Unit coverage for ArtworkStore.

Everything here is about not trusting the caller. The filename, the declared content
type and the declared size are all things the client controls; the bytes are the only
evidence. These tests exist so that stays true.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.config.schema import MediaConfig
from app.services.artwork_storage import MAX_ARTWORK_BYTES, ArtworkStore

# Minimal but genuine headers, padded so each clears the 16-byte sniff window.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 32
AVIF = b"\x00\x00\x00\x20" + b"ftyp" + b"avif" + b"\x00" * 32


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
