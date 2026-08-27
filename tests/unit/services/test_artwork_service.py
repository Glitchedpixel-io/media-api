# tests/unit/services/test_artwork_service.py
"""Unit coverage for the artwork services.

The service exists to own the two integrity checks the schema cannot: that the
submitted kind code resolves, and that the entity being decorated actually exists.
The second is the application-layer half of the typed association pattern -- there is
no foreign key on ``artwork.entity_id``, because its target table depends on
``entity_type``. If these tests go, nothing catches artwork pointing into space.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest
from fastapi import HTTPException

from app.repositories.errors import NotFoundError
from app.repositories.protocols import (
    ArtworkKindRepository,
    ArtworkRepository,
    MediaRepository,
    TitleRepository,
)
from app.schemas import (
    ArtworkCreatePublic,
    ArtworkKindCreatePublic,
    ArtworkKindPatchPublic,
    ArtworkKindRead,
    ArtworkPatchPublic,
    ArtworkRead,
)
from app.schemas.enums import EntityTypeEnum
from app.services import ArtworkKindService, ArtworkService

PATH_A = "ab/12/" + "ab12" + "0" * 60 + ".jpg"

POSTER = ArtworkKindRead(id=7, code="poster", label="Poster", description=None)


def _read(**overrides) -> ArtworkRead:
    defaults = dict(
        id=1,
        entity_type=EntityTypeEnum.title,
        entity_id=42,
        artwork_kind="poster",
        storage_path=PATH_A,
        mime="image/jpeg",
        width=1000,
        height=1500,
        is_primary=False,
        source_scheme_id=None,
        source_external_id=None,
        source_url=None,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
    )
    return ArtworkRead(**{**defaults, **overrides})


@pytest.fixture
def repo():
    return create_autospec(ArtworkRepository, instance=True)


@pytest.fixture
def kinds():
    mock = create_autospec(ArtworkKindRepository, instance=True)
    mock.get_by_code.return_value = POSTER
    return mock


@pytest.fixture
def titles():
    mock = create_autospec(TitleRepository, instance=True)
    mock.exists.return_value = True
    return mock


@pytest.fixture
def assets():
    mock = create_autospec(MediaRepository, instance=True)
    mock.exists.return_value = True
    return mock


@pytest.fixture
def service(repo, kinds, titles, assets) -> ArtworkService:
    return ArtworkService(repo, kinds, titles, assets)


def _create_payload(**overrides) -> ArtworkCreatePublic:
    defaults = dict(
        artwork_kind="poster",
        storage_path=PATH_A,
        mime="image/jpeg",
        width=1000,
        height=1500,
        is_primary=False,
    )
    return ArtworkCreatePublic(**{**defaults, **overrides})


@pytest.mark.unit
class TestEntityIntegrity:
    """The check no foreign key can perform."""

    def test_create_against_a_missing_title_is_404(self, service, titles, repo):
        titles.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.create_artwork(EntityTypeEnum.title, 42, _create_payload())
        assert exc.value.status_code == 404
        repo.create.assert_not_called()

    def test_create_against_a_missing_asset_is_404(self, service, assets, repo):
        assets.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.create_artwork(EntityTypeEnum.asset, 42, _create_payload())
        assert exc.value.status_code == 404
        repo.create.assert_not_called()

    def test_the_entity_type_selects_which_repository_is_asked(self, service, titles, assets, repo):
        """A title id checked against assets would pass for the wrong reason."""
        repo.create.return_value = _read()
        service.create_artwork(EntityTypeEnum.title, 42, _create_payload())
        titles.exists.assert_called_once_with(42)
        assets.exists.assert_not_called()

    def test_listing_for_a_missing_entity_is_404(self, service, titles):
        titles.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.list_artwork(EntityTypeEnum.title, 42)
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestKindResolution:

    def test_the_code_is_translated_to_an_id_before_persisting(self, service, repo):
        repo.create.return_value = _read()
        service.create_artwork(EntityTypeEnum.title, 42, _create_payload())

        internal = repo.create.call_args.args[0]
        assert internal.artwork_kind_id == POSTER.id
        # The persistence model forbids extras, so a leaked code would have raised.
        assert not hasattr(internal, "artwork_kind")

    def test_an_unknown_kind_is_422_not_500(self, service, kinds, repo):
        """A caller's bad kind code is a client error. Collapsing it into something
        that maps to 500 is what CLAUDE.md warns QuietClientErrorRoute cannot undo."""
        kinds.get_by_code.return_value = None
        with pytest.raises(HTTPException) as exc:
            service.create_artwork(EntityTypeEnum.title, 42, _create_payload())
        assert exc.value.status_code == 422
        repo.create.assert_not_called()

    def test_filtering_a_list_by_an_unknown_kind_is_422(self, service, kinds):
        kinds.get_by_code.return_value = None
        with pytest.raises(HTTPException) as exc:
            service.list_artwork(EntityTypeEnum.title, 42, kind="nonsense")
        assert exc.value.status_code == 422

    def test_an_unfiltered_list_does_not_resolve_a_kind(self, service, kinds, repo):
        repo.list_for_entity.return_value = []
        service.list_artwork(EntityTypeEnum.title, 42)
        kinds.get_by_code.assert_not_called()

    def test_updating_the_kind_translates_the_code(self, service, repo):
        repo.update.return_value = _read()
        service.update_artwork(1, ArtworkPatchPublic(artwork_kind="poster"), exclude_none=True)
        internal = repo.update.call_args.args[1]
        assert internal.artwork_kind_id == POSTER.id

    def test_an_update_that_omits_the_kind_leaves_it_alone(self, service, repo, kinds):
        repo.update.return_value = _read()
        service.update_artwork(1, ArtworkPatchPublic(width=99), exclude_none=True)
        kinds.get_by_code.assert_not_called()
        internal = repo.update.call_args.args[1]
        assert not internal.model_dump(exclude_unset=True).get("artwork_kind_id")


@pytest.mark.unit
class TestReads:

    def test_get_artwork_returns_the_row(self, service, repo):
        repo.get.return_value = _read()
        assert service.get_artwork(1).id == 1

    def test_get_artwork_is_404_when_missing(self, service, repo):
        repo.get.return_value = None
        with pytest.raises(HTTPException) as exc:
            service.get_artwork(1)
        assert exc.value.status_code == 404

    def test_no_primary_artwork_returns_none_rather_than_raising(self, service, repo):
        """The browse grid renders a placeholder for this; it is not a client error."""
        repo.get_primary.return_value = None
        assert service.get_primary_artwork(EntityTypeEnum.title, 42, "poster") is None

    def test_primary_artwork_is_looked_up_by_resolved_kind_id(self, service, repo):
        repo.get_primary.return_value = _read(is_primary=True)
        service.get_primary_artwork(EntityTypeEnum.title, 42, "poster")
        repo.get_primary.assert_called_once_with(EntityTypeEnum.title, 42, POSTER.id)


@pytest.mark.unit
class TestWrites:

    def test_set_primary_delegates(self, service, repo):
        repo.set_primary.return_value = _read(is_primary=True)
        assert service.set_primary_artwork(1).is_primary is True

    def test_set_primary_on_a_missing_row_is_404(self, service, repo):
        repo.set_primary.side_effect = NotFoundError
        with pytest.raises(HTTPException) as exc:
            service.set_primary_artwork(1)
        assert exc.value.status_code == 404

    def test_delete_delegates(self, service, repo):
        service.delete_artwork(1)
        repo.delete.assert_called_once_with(1)

    def test_delete_on_a_missing_row_is_404(self, service, repo):
        repo.delete.side_effect = NotFoundError
        with pytest.raises(HTTPException) as exc:
            service.delete_artwork(1)
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestProvenanceValidation:
    """``ck_artwork_source_pair`` refuses half a pair; the schema catches it first so
    the client gets a validation error naming the fields."""

    def test_a_scheme_without_an_id_is_rejected(self):
        with pytest.raises(ValueError):
            _create_payload(source_scheme_id=1)

    def test_an_id_without_a_scheme_is_rejected(self):
        with pytest.raises(ValueError):
            _create_payload(source_external_id="abc123")

    def test_both_together_are_accepted(self):
        payload = _create_payload(source_scheme_id=1, source_external_id="abc123")
        assert payload.source_scheme_id == 1

    def test_neither_is_accepted(self):
        """What the #104 backfill registers for a cover simply found on disk."""
        assert _create_payload().source_scheme_id is None

    def test_a_source_url_alone_is_fine(self):
        payload = _create_payload(source_url="https://example.com/poster.jpg")
        assert payload.source_url == "https://example.com/poster.jpg"

    @pytest.mark.parametrize("field", ["width", "height"])
    def test_dimensions_must_be_positive(self, field):
        with pytest.raises(ValueError):
            _create_payload(**{field: 0})


@pytest.mark.unit
class TestArtworkKindService:

    @pytest.fixture
    def kind_service(self, kinds) -> ArtworkKindService:
        return ArtworkKindService(kinds)

    def test_get_returns_the_kind(self, kind_service, kinds):
        kinds.get.return_value = POSTER
        assert kind_service.get_artwork_kind(7) == POSTER

    def test_get_is_404_when_missing(self, kind_service, kinds):
        kinds.get.return_value = None
        with pytest.raises(HTTPException) as exc:
            kind_service.get_artwork_kind(1)
        assert exc.value.status_code == 404

    def test_delete_refuses_a_kind_in_use_with_409(self, kind_service, kinds):
        """409, not the 422 a raw ForeignKeyViolation would map to -- the resource
        exists, it is just still referenced. Same reasoning as TitleTypeService."""
        kinds.exists.return_value = True
        kinds.usage_count.return_value = 3
        with pytest.raises(HTTPException) as exc:
            kind_service.delete_artwork_kind(1)
        assert exc.value.status_code == 409
        assert "3" in str(exc.value.detail)
        kinds.delete.assert_not_called()

    def test_delete_of_an_unused_kind_proceeds(self, kind_service, kinds):
        kinds.exists.return_value = True
        kinds.usage_count.return_value = 0
        kind_service.delete_artwork_kind(1)
        kinds.delete.assert_called_once_with(1)

    def test_delete_of_a_missing_kind_is_404(self, kind_service, kinds):
        kinds.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            kind_service.delete_artwork_kind(1)
        assert exc.value.status_code == 404

    def test_create_normalises_the_code(self, kind_service, kinds):
        kinds.create.return_value = POSTER
        kind_service.create_artwork_kind(ArtworkKindCreatePublic(code="  POSTER  ", label="Poster"))
        assert kinds.create.call_args.args[0].code == "poster"

    def test_a_blank_code_is_rejected(self):
        with pytest.raises(ValueError):
            ArtworkKindCreatePublic(code="   ", label="Poster")

    def test_update_delegates(self, kind_service, kinds):
        kinds.update.return_value = POSTER
        kind_service.update_artwork_kind(
            1, ArtworkKindPatchPublic(label="Cover"), exclude_none=True
        )
        kinds.update.assert_called_once()

    def test_list_delegates(self, kind_service, kinds):
        kinds.list_all.return_value = [POSTER]
        assert kind_service.get_artwork_kinds() == [POSTER]
