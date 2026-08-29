"""
FastAPI Integration Tests for the Artwork API

Drives the whole registration path: multipart request -> router -> service ->
ArtworkStore -> filesystem, and service -> repository -> database.

The unit tests prove the store and the service in isolation. What only this level can
show is that the two halves agree -- that the row the API returns actually describes a
file that exists on disk at the path it names, and that a refusal leaves neither.
"""

from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.repositories import SQLAlchemyIdSchemeRepository
from app.repositories.protocols import MediaRepository, TitleRepository
from app.schemas import AssetCreateInternal, IdSchemeCreateInternal, TitleCreateInternal
from app.services.artwork_storage import MAX_ARTWORK_BYTES

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
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
            files={"file": ("bd.png", PNG, "image/png")},
            data={"artwork_kind": "backdrop", "is_primary": "true"},
        ).json()

        assert poster["is_primary"] is True
        assert backdrop["is_primary"] is True


@pytest.mark.integration
class TestRecordLifecycle:

    def test_patch_updates_metadata(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(f"/api/artwork/{created['id']}", json={"width": 1200})
        assert response.status_code == HTTPStatus.OK
        assert response.json()["width"] == 1200

    def test_patch_can_change_the_kind(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(f"/api/artwork/{created['id']}", json={"artwork_kind": "backdrop"})
        assert response.json()["artwork_kind"] == "backdrop"

    def test_patch_with_an_unknown_kind_is_422(self, client, title_id):
        created = _upload(client, f"/api/titles/{title_id}/artwork").json()
        response = client.patch(f"/api/artwork/{created['id']}", json={"artwork_kind": "nonsense"})
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_patch_on_a_missing_record_is_404(self, client):
        assert client.patch("/api/artwork/999999", json={"width": 10}).status_code == 404

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

    def test_a_non_positive_dimension_is_422(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", JPEG, "image/jpeg")},
            data={"artwork_kind": "poster", "width": "0"},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_a_missing_kind_is_422(self, client, title_id):
        response = client.post(
            f"/api/titles/{title_id}/artwork",
            files={"file": ("poster.jpg", JPEG, "image/jpeg")},
            data={},
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
