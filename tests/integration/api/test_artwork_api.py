"""
FastAPI Integration Tests for the Artwork API

Drives the whole registration path: multipart request -> router -> service ->
ArtworkStore -> filesystem, and service -> repository -> database.

The unit tests prove the store and the service in isolation. What only this level can
show is that the two halves agree -- that the row the API returns actually describes a
file that exists on disk at the path it names, and that a refusal leaves neither.
"""

import io
from http import HTTPStatus
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.repositories import SQLAlchemyIdSchemeRepository
from app.repositories.protocols import MediaRepository, TitleRepository
from app.schemas import AssetCreateInternal, IdSchemeCreateInternal, TitleCreateInternal
from app.services.artwork_storage import MAX_ARTWORK_BYTES


def _image_bytes(width: int = 600, height: int = 900, fmt: str = "JPEG") -> bytes:
    """A real, decodable image, poster-shaped by default.

    The store measures every upload and refuses what it cannot read (#140), so a
    plausible magic number with padding no longer gets through it. Since #153 it also
    refuses a shape the declared kind forbids, so the default has to be a valid poster
    -- 2:3 and clear of the width floor -- because that is what most of these upload.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format=fmt)
    return buffer.getvalue()


JPEG = _image_bytes(fmt="JPEG")
PNG = _image_bytes(fmt="PNG")
NOT_AN_IMAGE = b"<!doctype html><h1>not a poster</h1>" + b" " * 32


@pytest.fixture
def artwork_root(test_settings: AppConfig) -> Path:
    return Path(test_settings.media.artwork_root)


@pytest.fixture
def title_id(title_repository: TitleRepository, title_type_ids: dict[str, int]) -> int:
    return title_repository.create(
        TitleCreateInternal(name="A Title", title_type_id=title_type_ids["movie"])
    ).id


@pytest.fixture
def id_scheme_id(db_session) -> int:
    """A scheme for the provenance pair to point at.

    Created directly rather than through a fixture because there is no
    id_scheme_repository fixture at this level; committed so the request's own session
    sees it.
    """
    return (
        SQLAlchemyIdSchemeRepository(db_session)
        .create(IdSchemeCreateInternal(code="test", label="Test scheme", validator=None))
        .id
    )


@pytest.fixture
def asset_id(media_repository: MediaRepository) -> int:
    return media_repository.create(
        AssetCreateInternal(
            path="movies/a.mkv",
            filename="a.mkv",
            duration=1.0,
            bitrate=1,
            container_format="matroska",
            size=1,
            mtime=None,
            last_seen=None,
            master_asset_id=None,
        )
    ).id


def _upload(client: TestClient, url: str, payload: bytes = JPEG, **form) -> object:
    data = {"artwork_kind": "poster", **{k: str(v) for k, v in form.items()}}
    return client.post(url, files={"file": ("poster.jpg", payload, "image/jpeg")}, data=data)


@pytest.mark.integration
class TestUploadTitleArtwork:

    def test_upload_returns_201_and_persists(self, client, title_id, artwork_root):
        response = _upload(client, f"/api/titles/{title_id}/artwork")
        assert response.status_code == HTTPStatus.CREATED

        body = response.json()
        assert body["artwork_kind"] == "poster"
        assert body["entity_id"] == title_id
        assert body["entity_type"] == "title"
        assert body["mime"] == "image/jpeg"

        # The claim worth checking at this level: the row describes a real file.
        assert (artwork_root / body["storage_path"]).read_bytes() == JPEG

    def test_the_stored_file_is_content_addressed(self, client, title_id, artwork_root):
        import hashlib

        body = _upload(client, f"/api/titles/{title_id}/artwork").json()
        digest = hashlib.sha256(JPEG).hexdigest()
        assert body["storage_path"] == f"{digest[:2]}/{digest[2:4]}/{digest}.jpg"

    def test_uploaded_artwork_appears_in_the_list(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        listed = client.get(f"/api/titles/{title_id}/artwork").json()
        assert [a["id"] for a in listed] == [created["id"]]

    def test_the_mime_comes_from_the_bytes_not_the_declared_type(self, client, title_id):
        """The client says image/jpeg and names the file .png; the bytes are PNG, and
        the bytes win."""
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.png", PNG, "image/jpeg")},
            data={"artwork_kind": "poster"},
        )
        assert response.status_code == HTTPStatus.CREATED
        body = response.json()
        assert body["mime"] == "image/png"
        assert body["storage_path"].endswith(".png")

    def test_a_non_image_is_415_and_stores_nothing(self, client, title_id, artwork_root):
        response = _upload(client, f"/api/titles/{title_id}/artwork", payload=NOT_AN_IMAGE)
        assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
        assert list(artwork_root.rglob("*.*")) == []
        assert client.get(f"/api/titles/{title_id}/artwork").json() == []

    def test_an_oversized_upload_is_413(self, client, title_id, artwork_root):
        big = JPEG + b"\x00" * (MAX_ARTWORK_BYTES + 1)
        response = _upload(client, f"/api/titles/{title_id}/artwork", payload=big)
        assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        assert list(artwork_root.rglob("*.*")) == []

    def test_an_unknown_title_is_404_and_writes_nothing(self, client, artwork_root):
        """The entity check runs before the bytes are touched, so a mistyped ID does
        not leave an orphan file behind for every retry."""
        response = _upload(client, "/api/titles/999999/artwork")
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert list(artwork_root.rglob("*.*")) == []

    def test_an_unknown_kind_is_422_and_writes_nothing(self, client, title_id, artwork_root):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", JPEG, "image/jpeg")},
            data={"artwork_kind": "nonsense"},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert list(artwork_root.rglob("*.*")) == []

    def test_the_same_file_twice_for_one_title_is_409(self, client, title_id):
        assert _upload(client, f"/api/titles/{title_id}/artwork").status_code == 201
        assert _upload(client, f"/api/titles/{title_id}/artwork").status_code == 409

    def test_two_titles_may_share_one_file(
        self, client, title_repository, title_type_ids, artwork_root
    ):
        """The reason for content addressing: a season and its episodes share one
        poster, stored once and referenced twice."""
        a = title_repository.create(
            TitleCreateInternal(name="Season 1", title_type_id=title_type_ids["season"])
        ).id
        b = title_repository.create(
            TitleCreateInternal(name="Episode 1", title_type_id=title_type_ids["episode"])
        ).id

        first = _upload(client, f"/api/titles/{a}/artwork").json()
        second = _upload(client, f"/api/titles/{b}/artwork").json()

        assert first["storage_path"] == second["storage_path"]
        assert len(list(artwork_root.rglob("*.jpg"))) == 1


@pytest.mark.integration
class TestUploadAssetArtwork:

    def test_upload_against_an_asset(self, client, asset_id, artwork_root):
        response = _upload(client, f"/api/assets/{asset_id}/artwork")
        assert response.status_code == HTTPStatus.CREATED
        body = response.json()
        assert body["entity_type"] == "asset"
        assert body["entity_id"] == asset_id
        assert (artwork_root / body["storage_path"]).exists()

    def test_an_unknown_asset_is_404(self, client):
        assert _upload(client, "/api/assets/999999/artwork").status_code == HTTPStatus.NOT_FOUND

    def test_a_title_and_an_asset_do_not_share_a_listing(self, client, title_id, asset_id):
        _upload(client, f"/api/titles/{title_id}/artwork", payload=JPEG)
        _upload(client, f"/api/assets/{asset_id}/artwork", payload=PNG)

        title_listing = client.get(f"/api/titles/{title_id}/artwork").json()
        asset_listing = client.get(f"/api/assets/{asset_id}/artwork").json()

        assert [a["mime"] for a in title_listing] == ["image/jpeg"]
        assert [a["mime"] for a in asset_listing] == ["image/png"]


@pytest.mark.integration
class TestPrimary:

    def test_uploading_with_is_primary_sets_it(self, client, title_id):
        body = _upload(client, f"/api/titles/{title_id}/artwork", is_primary=True).json()
        assert body["is_primary"] is True

    def test_a_second_primary_upload_demotes_the_first(self, client, title_id):
        """Rather than 409ing on the unique index, which is what writing the flag
        straight through would do."""
        first = _upload(client, f"/api/titles/{title_id}/artwork", is_primary=True).json()
        second = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("other.png", PNG, "image/png")},
            data={"artwork_kind": "poster", "is_primary": "true"},
        ).json()

        assert second["is_primary"] is True
        assert client.get(f"/api/artwork/{first['id']}").json()["is_primary"] is False

    def test_patching_is_primary_promotes_and_demotes(self, client, title_id):
        first = _upload(client, f"/api/titles/{title_id}/artwork", is_primary=True).json()
        second = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("other.png", PNG, "image/png")},
            data={"artwork_kind": "poster"},
        ).json()

        promoted = client.patch(f"/api/artwork/{second['id']}", json={"is_primary": True})
        assert promoted.status_code == HTTPStatus.OK
        assert promoted.json()["is_primary"] is True
        assert client.get(f"/api/artwork/{first['id']}").json()["is_primary"] is False

    def test_the_primary_sorts_first_in_the_listing(self, client, title_id):
        _upload(client, f"/api/titles/{title_id}/artwork")
        primary = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("other.png", PNG, "image/png")},
            data={"artwork_kind": "poster", "is_primary": "true"},
        ).json()

        listing = client.get(f"/api/titles/{title_id}/artwork").json()
        assert listing[0]["id"] == primary["id"]

    def test_a_title_may_hold_a_primary_of_each_kind(self, client, title_id):
        poster = _upload(client, f"/api/titles/{title_id}/artwork", is_primary=True).json()
        backdrop = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("bd.png", _image_bytes(1920, 1080, "PNG"), "image/png")},
            data={"artwork_kind": "backdrop", "is_primary": "true"},
        ).json()

        assert poster["is_primary"] is True
        assert backdrop["is_primary"] is True


@pytest.mark.integration
class TestRecordLifecycle:

    def test_patch_updates_metadata(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(
            f"/api/artwork/{created['id']}", json={"source_url": "https://example.test/a.jpg"}
        )
        assert response.status_code == HTTPStatus.OK
        assert response.json()["source_url"] == "https://example.test/a.jpg"

    @pytest.mark.parametrize(
        "payload",
        [
            {"storage_path": "ab/12/" + "cd" * 32 + ".jpg"},
            {"mime": "image/png"},
            {"width": 1200},
            {"height": 800},
        ],
    )
    def test_patch_refuses_a_server_discovered_field(self, client, title_id, payload):
        """The server established these from the uploaded bytes. Accepting a patch of
        them would let a client rewrite what we hold about a file, or repoint a row at
        another entity's image while keeping this one's dimensions. See #139."""
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(f"/api/artwork/{created['id']}", json=payload)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_a_refused_patch_changes_nothing(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        client.patch(f"/api/artwork/{created['id']}", json={"width": 1200})
        after = client.get(f"/api/artwork/{created['id']}").json()
        assert after["width"] == created["width"]
        assert after["storage_path"] == created["storage_path"]

    def test_patch_can_change_the_kind(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(f"/api/artwork/{created['id']}", json={"artwork_kind": "backdrop"})
        assert response.json()["artwork_kind"] == "backdrop"

    def test_patch_with_an_unknown_kind_is_422(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(f"/api/artwork/{created['id']}", json={"artwork_kind": "nonsense"})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_patch_on_a_missing_record_is_404(self, client):
        patch = {"source_url": "https://example.test/a.jpg"}
        assert client.patch("/api/artwork/999999", json=patch).status_code == 404

    def test_delete_removes_the_record(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        assert client.delete(f"/api/artwork/{created['id']}").status_code == 204
        assert client.get(f"/api/artwork/{created['id']}").status_code == 404

    def test_delete_leaves_the_file_on_disk(self, client, title_id, artwork_root):
        """Content addressing means other rows may point at the same bytes, so
        deleting a record must not delete the file."""
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        client.delete(f"/api/artwork/{created['id']}")
        assert (artwork_root / created["storage_path"]).exists()

    def test_delete_on_a_missing_record_is_404(self, client):
        assert client.delete("/api/artwork/999999").status_code == 404

    def test_get_on_a_missing_record_is_404(self, client):
        assert client.get("/api/artwork/999999").status_code == 404


@pytest.mark.integration
class TestArtworkKinds:

    def test_the_seeded_kinds_are_listed(self, client):
        codes = {k["code"] for k in client.get("/api/artwork_kinds").json()}
        assert {"poster", "backdrop", "thumbnail", "logo", "banner", "still"} <= codes

    def test_listing_is_ordered_by_code(self, client):
        codes = [k["code"] for k in client.get("/api/artwork_kinds").json()]
        assert codes == sorted(codes)


@pytest.mark.integration
class TestUploadFormValidation:
    """The form's cross-field rules have to reach the client as 422s.

    A pydantic ValidationError raised inside a dependency escapes as a 500 unless it
    is translated, and a 500 is what Logfire pages on -- the failure CLAUDE.md warns
    QuietClientErrorRoute cannot undo, because by then the status is already wrong.
    """

    def test_half_a_provenance_pair_is_422_not_500(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", JPEG, "image/jpeg")},
            data={"artwork_kind": "poster", "source_scheme_id": "1"},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_both_halves_together_are_accepted(self, client, title_id, id_scheme_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", JPEG, "image/jpeg")},
            data={
                "artwork_kind": "poster",
                "source_scheme_id": str(id_scheme_id),
                "source_external_id": "abc123",
            },
        )
        assert response.status_code == HTTPStatus.CREATED
        assert response.json()["source_external_id"] == "abc123"

    def test_the_recorded_dimensions_are_measured_from_the_bytes(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", _image_bytes(400, 600), "image/jpeg")},
            data={"artwork_kind": "poster"},
        )
        assert response.status_code == HTTPStatus.CREATED
        assert (response.json()["width"], response.json()["height"]) == (400, 600)

    def test_submitted_dimensions_are_ignored_in_favour_of_the_real_ones(self, client, title_id):
        """The form no longer carries width or height, and FastAPI drops undeclared
        multipart fields -- so a producer still sending them is harmless rather than
        rejected. What matters is that its numbers cannot reach the row: a submitted
        size is a claim about a file the caller also controls (#141)."""
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", _image_bytes(400, 600), "image/jpeg")},
            data={"artwork_kind": "poster", "width": "1", "height": "1"},
        )
        assert response.status_code == HTTPStatus.CREATED
        assert (response.json()["width"], response.json()["height"]) == (400, 600)

    def test_a_missing_kind_is_422(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", JPEG, "image/jpeg")},
            data={},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


@pytest.mark.integration
class TestShapeEnforcement:
    """A declared kind whose shape the pixels contradict is refused (#153).

    Shape is necessary but not sufficient: the caller says what the artwork is, and
    this says whether the image can be that. Nothing infers a kind from pixels.
    """

    def test_a_landscape_image_is_not_a_poster(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("wrong.jpg", _image_bytes(1920, 1080), "image/jpeg")},
            data={"artwork_kind": "poster"},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "poster" in response.json()["detail"]

    def test_the_refusal_names_the_expectation(self, client, title_id):
        """A caller that is told only "no" cannot fix its request."""
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("wrong.jpg", _image_bytes(1920, 1080), "image/jpeg")},
            data={"artwork_kind": "poster"},
        )
        detail = response.json()["detail"]
        assert "1920x1080" in detail
        assert "aspect ratio" in detail

    def test_a_portrait_image_is_accepted_as_a_poster(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("right.jpg", _image_bytes(600, 900), "image/jpeg")},
            data={"artwork_kind": "poster"},
        )
        assert response.status_code == HTTPStatus.CREATED

    def test_the_tolerance_admits_the_row_it_exists_for(self, client, title_id):
        """499x500 is a real production cover, 0.2% off square. The tolerance exists
        because of it, so a rule that refused it would be the wrong rule (#151)."""
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("cover.jpg", _image_bytes(499, 500), "image/jpeg")},
            data={"artwork_kind": "cover_art"},
        )
        assert response.status_code == HTTPStatus.CREATED

    def test_a_kind_with_no_target_ratio_accepts_any_shape(self, client, title_id):
        """`thumbnail` holds both 16:9 and 4:3 real rows, so it constrains width only."""
        for width, height in ((1280, 720), (640, 480)):
            response = client.post(
                f"/api/titles/{title_id}/artwork",
                files={"file": ("t.jpg", _image_bytes(width, height), "image/jpeg")},
                data={"artwork_kind": "thumbnail"},
            )
            assert response.status_code == HTTPStatus.CREATED, f"{width}x{height} refused"

    def test_an_image_below_the_width_floor_is_refused(self, client, title_id):
        """128x96 is the one stored row too small to be useful artwork of any kind."""
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("tiny.jpg", _image_bytes(128, 96), "image/jpeg")},
            data={"artwork_kind": "thumbnail"},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "wide" in response.json()["detail"]

    def test_a_refused_upload_leaves_nothing_on_disk(self, client, title_id, artwork_root):
        """The constraint that shaped the design. Deleting a committed file is not a
        substitute: content addressing means it may already be shared with another row,
        so the refusal has to happen before anything is committed."""
        before = sorted(p.name for p in artwork_root.rglob("*") if p.is_file())

        client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("wrong.jpg", _image_bytes(1920, 1080), "image/jpeg")},
            data={"artwork_kind": "poster"},
        )

        after = sorted(p.name for p in artwork_root.rglob("*") if p.is_file())
        assert after == before
        assert not list(artwork_root.rglob("*.part"))

    def test_a_refusal_does_not_delete_a_file_another_row_shares(
        self, client, title_id, artwork_root
    ):
        """Content addressing deduplicates, so the bytes a rejected upload carries may
        already be the file a valid row points at. Cleaning up after the fact would
        break that row; refusing before the commit cannot."""
        payload = _image_bytes(1280, 720)
        created = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("ok.jpg", payload, "image/jpeg")},
            data={"artwork_kind": "thumbnail"},
        ).json()
        stored_path = artwork_root / created["storage_path"]
        assert stored_path.is_file()

        # The same bytes, now declared as something they cannot be.
        client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("wrong.jpg", payload, "image/jpeg")},
            data={"artwork_kind": "poster"},
        )

        assert stored_path.is_file(), "the rejected upload deleted a file in use"
        assert client.get(f"/api/artwork/{created['id']}").status_code == HTTPStatus.OK

    def test_an_unconstrained_kind_accepts_anything(self, client, title_id):
        """`unknown` is the absence of a claim, so it cannot be contradicted."""
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("odd.jpg", _image_bytes(1000, 137), "image/jpeg")},
            data={"artwork_kind": "unknown"},
        )
        assert response.status_code == HTTPStatus.CREATED
