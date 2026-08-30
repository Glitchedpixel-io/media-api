# tests/unit/services/test_artwork_service.py
"""Unit coverage for the artwork services.

The service exists to own the two integrity checks the schema cannot: that the
submitted kind code resolves, and that the entity being decorated actually exists.
The second is the application-layer half of the typed association pattern -- there is
no foreign key on ``artwork.entity_id``, because its target table depends on
``entity_type``. If these tests go, nothing catches artwork pointing into space.
"""

from __future__ import annotations

import io
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
from app.models import DEFAULT_ARTWORK_KINDS
from app.schemas import (
    ArtworkKindCreatePublic,
    ArtworkKindPatchPublic,
    ArtworkKindRead,
    ArtworkPatchPublic,
    ArtworkRead,
    ArtworkUploadForm,
)
from app.schemas.enums import EntityTypeEnum
from app.services import ArtworkKindService, ArtworkService, ArtworkStore, StoredArtwork

PATH_A = "ab/12/" + "ab12" + "0" * 60 + ".jpg"

POSTER = ArtworkKindRead(id=7, code="poster", label="Poster", description=None)


def _stored(**overrides) -> StoredArtwork:
    """What the store hands back. Its four measured fields are the only source
    register_stored will accept for them, which is the point of #142."""
    defaults = dict(
        digest="ab12" + "0" * 60,
        suffix=".jpg",
        mime="image/jpeg",
        size=1234,
        width=1000,
        height=1500,
        storage_path=PATH_A,
        already_present=False,
    )
    return StoredArtwork(**{**defaults, **overrides})


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
def store():
    mock = create_autospec(ArtworkStore, instance=True)
    mock.store.return_value = StoredArtwork(
        digest="ab12" + "0" * 60,
        suffix=".jpg",
        mime="image/jpeg",
        size=1234,
        width=1000,
        height=1500,
        storage_path=PATH_A,
        already_present=False,
    )
    return mock


@pytest.fixture
def service(repo, kinds, titles, assets, store) -> ArtworkService:
    return ArtworkService(repo, kinds, titles, assets, store)


def _upload(**overrides) -> ArtworkUploadForm:
    defaults = dict(artwork_kind="poster", is_primary=False)
    return ArtworkUploadForm(**{**defaults, **overrides})


@pytest.mark.unit
class TestEntityIntegrity:
    """The check no foreign key can perform."""

    def test_create_against_a_missing_title_is_404(self, service, titles, repo):
        titles.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.register_stored(EntityTypeEnum.title, 42, POSTER.id, _stored())
        assert exc.value.status_code == 404
        repo.create.assert_not_called()

    def test_create_against_a_missing_asset_is_404(self, service, assets, repo):
        assets.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.register_stored(EntityTypeEnum.asset, 42, POSTER.id, _stored())
        assert exc.value.status_code == 404
        repo.create.assert_not_called()

    def test_the_entity_type_selects_which_repository_is_asked(self, service, titles, assets, repo):
        """A title id checked against assets would pass for the wrong reason."""
        repo.create.return_value = _read()
        service.register_stored(EntityTypeEnum.title, 42, POSTER.id, _stored())
        titles.exists.assert_called_with(42)
        assets.exists.assert_not_called()

    def test_an_upload_checks_the_entity_before_writing_any_bytes(self, service, titles, store):
        """Checked twice on the upload path, deliberately. This one is the fail-fast:
        without it the store writes a file for an entity that does not exist, and the
        404 from register_stored leaves it orphaned under ARTWORK_ROOT. The check
        inside register_stored is the invariant at the point of insert."""
        titles.exists.return_value = False
        with pytest.raises(HTTPException):
            service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        store.store.assert_not_called()

    def test_listing_for_a_missing_entity_is_404(self, service, titles):
        titles.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.list_artwork(EntityTypeEnum.title, 42)
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestKindResolution:

    def test_the_code_is_translated_to_an_id_before_persisting(self, service, repo):
        """Resolution happens on the upload path. register_stored takes an id that is
        already resolved, which is why the backfill -- which knows its kind up front --
        does not pay for a lookup per row."""
        repo.create.return_value = _read()
        service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))

        internal = repo.create.call_args.args[0]
        assert internal.artwork_kind_id == POSTER.id
        # The persistence model forbids extras, so a leaked code would have raised.
        assert not hasattr(internal, "artwork_kind")

    def test_an_unknown_kind_is_422_not_500(self, service, kinds, repo, store):
        """A caller's bad kind code is a client error. Collapsing it into something
        that maps to 500 is what CLAUDE.md warns QuietClientErrorRoute cannot undo."""
        kinds.get_by_code.return_value = None
        with pytest.raises(HTTPException) as exc:
            service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        assert exc.value.status_code == 422
        repo.create.assert_not_called()
        store.store.assert_not_called(), "the kind is settled before any bytes are written"

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
        service.update_artwork(
            1, ArtworkPatchPublic(source_url="https://example.test/a.jpg"), exclude_none=True
        )
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
            _read(source_scheme_id=1)

    def test_an_id_without_a_scheme_is_rejected(self):
        with pytest.raises(ValueError):
            _read(source_external_id="abc123")

    def test_both_together_are_accepted(self):
        payload = _read(source_scheme_id=1, source_external_id="abc123")
        assert payload.source_scheme_id == 1

    def test_neither_is_accepted(self):
        """What the #104 backfill registers for a cover simply found on disk."""
        assert _read().source_scheme_id is None

    def test_a_source_url_alone_is_fine(self):
        payload = _read(source_url="https://example.com/poster.jpg")
        assert payload.source_url == "https://example.com/poster.jpg"

    @pytest.mark.parametrize("field", ["width", "height"])
    def test_dimensions_must_be_positive(self, field):
        with pytest.raises(ValueError):
            _read(**{field: 0})


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


@pytest.mark.unit
class TestRegisterUpload:
    """The ordering rule is the substance here.

    Everything checkable without the bytes is checked before anything reaches disk, so
    an ordinary client mistake never leaves a file behind. What remains -- file
    written, row rejected -- is the deliberate direction, because a content-addressed
    orphan is inert while a row pointing at nothing renders as a broken image forever.
    """

    def test_a_successful_upload_stores_then_registers(self, service, repo, store):
        repo.create.return_value = _read()
        service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))

        store.store.assert_called_once()
        internal = repo.create.call_args.args[0]
        assert internal.storage_path == PATH_A
        assert internal.mime == "image/jpeg"

    def test_the_path_and_mime_come_from_the_store_not_the_caller(self, service, repo, store):
        """ArtworkUploadForm carries neither field, so there is nothing to spoof --
        this asserts the derived values are what actually get persisted."""
        repo.create.return_value = _read()
        service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        internal = repo.create.call_args.args[0]
        assert (internal.storage_path, internal.mime) == (
            store.store.return_value.storage_path,
            store.store.return_value.mime,
        )

    def test_a_missing_entity_is_rejected_before_the_file_is_written(
        self, service, titles, store, repo
    ):
        titles.exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        assert exc.value.status_code == 404
        store.store.assert_not_called()
        repo.create.assert_not_called()

    def test_an_unknown_kind_is_rejected_before_the_file_is_written(
        self, service, kinds, store, repo
    ):
        """The cheapest check that can fail must fail first, or a typo in a kind code
        leaves an orphan for every retry."""
        kinds.get_by_code.return_value = None
        with pytest.raises(HTTPException) as exc:
            service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        assert exc.value.status_code == 422
        store.store.assert_not_called()
        repo.create.assert_not_called()

    def test_a_refused_file_never_reaches_the_repository(self, service, store, repo):
        store.store.side_effect = HTTPException(status_code=415, detail="not an image")
        with pytest.raises(HTTPException) as exc:
            service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        assert exc.value.status_code == 415
        repo.create.assert_not_called()

    def test_the_insert_never_carries_is_primary(self, service, repo, store):
        """A second primary would collide with the unique index and surface as a 409
        for what the caller reasonably means. Promotion happens after the insert."""
        repo.create.return_value = _read()
        repo.set_primary.return_value = _read(is_primary=True)
        service.register_upload(
            EntityTypeEnum.title, 42, _upload(is_primary=True), io.BytesIO(b"x")
        )
        assert repo.create.call_args.args[0].is_primary is False

    def test_requesting_primary_promotes_after_the_insert(self, service, repo, store):
        repo.create.return_value = _read(id=5)
        repo.set_primary.return_value = _read(id=5, is_primary=True)
        result = service.register_upload(
            EntityTypeEnum.title, 42, _upload(is_primary=True), io.BytesIO(b"x")
        )
        repo.set_primary.assert_called_once_with(5)
        assert result.is_primary is True

    def test_not_requesting_primary_leaves_the_incumbent_alone(self, service, repo, store):
        repo.create.return_value = _read()
        service.register_upload(
            EntityTypeEnum.title, 42, _upload(is_primary=False), io.BytesIO(b"x")
        )
        repo.set_primary.assert_not_called()

    def test_provenance_is_carried_through(self, service, repo, store):
        repo.create.return_value = _read()
        service.register_upload(
            EntityTypeEnum.title,
            42,
            _upload(source_scheme_id=1, source_external_id="abc", source_url="https://x/y.jpg"),
            io.BytesIO(b"x"),
        )
        internal = repo.create.call_args.args[0]
        assert (internal.source_scheme_id, internal.source_external_id) == (1, "abc")
        assert internal.source_url == "https://x/y.jpg"

    def test_half_a_provenance_pair_is_refused_by_the_form(self):
        with pytest.raises(ValueError):
            _upload(source_scheme_id=1)

    def test_uploading_against_an_asset_checks_the_asset_repository(
        self, service, assets, titles, repo, store
    ):
        repo.create.return_value = _read(entity_type=EntityTypeEnum.asset)
        service.register_upload(EntityTypeEnum.asset, 42, _upload(), io.BytesIO(b"x"))
        # Called twice by design -- fail-fast before the write, invariant at the
        # insert. What matters is that the *asset* repository is the one asked.
        assets.exists.assert_called_with(42)
        titles.exists.assert_not_called()


@pytest.mark.unit
class TestPrimaryViaPatch:
    """PATCH is how the DoD asks for primary to change, so is_primary cannot simply be
    written through -- it has to demote the incumbent."""

    def test_setting_primary_true_goes_through_set_primary(self, service, repo):
        repo.update.return_value = _read()
        repo.set_primary.return_value = _read(is_primary=True)
        result = service.update_artwork(1, ArtworkPatchPublic(is_primary=True), exclude_none=True)
        repo.set_primary.assert_called_once_with(1)
        assert result.is_primary is True

    def test_the_flag_is_not_written_straight_through(self, service, repo):
        """Writing it via repo.update would hit uq_artwork_one_primary_per_kind and
        turn "make this the poster" into a 409 the caller cannot act on."""
        repo.update.return_value = _read()
        repo.set_primary.return_value = _read(is_primary=True)
        service.update_artwork(1, ArtworkPatchPublic(is_primary=True), exclude_none=True)
        first_update = repo.update.call_args_list[0].args[1]
        assert "is_primary" not in first_update.model_dump(exclude_unset=True)

    def test_setting_primary_false_demotes_without_promoting_anything(self, service, repo):
        """An entity is allowed no primary at all -- a title whose poster was
        withdrawn should look like that, not silently promote a replacement."""
        repo.update.return_value = _read()
        service.update_artwork(1, ArtworkPatchPublic(is_primary=False), exclude_none=False)
        repo.set_primary.assert_not_called()
        assert repo.update.call_args.args[1].is_primary is False

    def test_other_fields_are_applied_alongside_a_promotion(self, service, repo):
        repo.update.return_value = _read()
        repo.set_primary.return_value = _read(is_primary=True)
        service.update_artwork(
            1,
            ArtworkPatchPublic(source_url="https://example.test/a.jpg", is_primary=True),
            exclude_none=True,
        )
        assert repo.update.call_args_list[0].args[1].source_url == "https://example.test/a.jpg"
        repo.set_primary.assert_called_once_with(1)

    def test_a_patch_touching_nothing_still_404s_for_a_missing_row(self, service, repo):
        """repo.update is called even with an empty payload, so the ID is still
        checked rather than the call silently succeeding."""
        repo.update.side_effect = NotFoundError
        with pytest.raises(HTTPException) as exc:
            service.update_artwork(1, ArtworkPatchPublic(), exclude_none=True)
        assert exc.value.status_code == 404


@pytest.mark.unit
class TestPatchRefusesDiscoveredFields:
    """The server establishes these from the uploaded bytes; a client that could patch
    them could undo every check ``ArtworkStore`` performs -- rewriting the measured
    dimensions, claiming a mime the bytes contradict, or repointing ``storage_path`` at
    another entity's file while keeping this row's dimensions. See #139."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("storage_path", "ab/12/" + "cd" * 32 + ".jpg"),
            ("mime", "image/png"),
            ("width", 900),
            ("height", 600),
        ],
    )
    def test_a_discovered_field_is_rejected(self, field, value):
        with pytest.raises(ValueError) as exc:
            ArtworkPatchPublic(**{field: value})
        # Named rather than silently dropped: a no-op would leave the caller believing
        # the write landed.
        assert field in str(exc.value)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("artwork_kind", "backdrop"),
            ("is_primary", True),
            ("source_url", "https://example.test/a.jpg"),
        ],
    )
    def test_an_asserted_field_is_accepted(self, field, value):
        assert getattr(ArtworkPatchPublic(**{field: value}), field) == value

    def test_provenance_still_travels_as_a_pair(self):
        with pytest.raises(ValueError):
            ArtworkPatchPublic(source_scheme_id=1)

    def test_the_service_never_sees_a_discovered_field(self, service, repo):
        """The schema is the only guard, so this pins the whole surface rather than
        one field: nothing reaching the repository can carry a discovered value."""
        repo.update.return_value = _read()
        service.update_artwork(1, ArtworkPatchPublic(artwork_kind="poster"), exclude_none=True)
        written = repo.update.call_args.args[1].model_dump(exclude_unset=True)
        assert not {"storage_path", "mime", "width", "height"} & written.keys()


@pytest.mark.unit
class TestUploadDimensionsComeFromTheStore:
    """The row records what the bytes turned out to be, not what the request said.

    The upload form no longer carries width or height at all, so this pins the other
    half: that the service reads them off the StoredArtwork the store returns, in the
    same way it already takes storage_path and mime from there (#141).
    """

    def test_the_measured_dimensions_reach_the_row(self, service, repo, store):
        service.register_upload(EntityTypeEnum.title, 42, _upload(), io.BytesIO(b"x"))
        written = repo.create.call_args.args[0]
        assert (written.width, written.height) == (
            store.store.return_value.width,
            store.store.return_value.height,
        )

    def test_the_form_cannot_carry_a_dimension(self):
        """extra="forbid" makes a stale caller's width a loud 422 at the model, which
        matters because the multipart layer would otherwise drop it silently."""
        for field in ("width", "height"):
            with pytest.raises(ValueError):
                _upload(**{field: 900})


@pytest.mark.unit
class TestKindShapeDefinition:
    """A kind's shape is a constraint the server checks a declared kind against, never a
    way to infer one (#127). These pin the definition; #153 enforces it."""

    def test_a_kind_may_expect_no_shape_at_all(self):
        """Null is the honest answer for a transparent logo and for unclassified
        artwork -- not a gap waiting to be filled."""
        kind = ArtworkKindCreatePublic(code="logo", label="Logo")
        assert kind.target_ratio is None
        assert kind.min_width is None

    def test_a_tolerance_without_a_target_is_rejected(self):
        """It has nothing to be a tolerance of."""
        with pytest.raises(ValueError) as exc:
            ArtworkKindCreatePublic(code="x", label="X", ratio_tolerance=0.02)
        assert "target_ratio" in str(exc.value)

    def test_a_width_range_that_admits_nothing_is_rejected(self):
        with pytest.raises(ValueError):
            ArtworkKindCreatePublic(code="x", label="X", min_width=900, max_width=100)

    @pytest.mark.parametrize("field", ["target_ratio", "min_width", "max_width"])
    def test_non_positive_shape_values_are_rejected(self, field):
        with pytest.raises(ValueError):
            ArtworkKindCreatePublic(code="x", label="X", **{field: 0})


@pytest.mark.unit
class TestSeededKinds:
    """The seed carries the decision from #127, including which numbers are measured."""

    def test_the_new_kinds_are_seeded(self):
        codes = {seed.code for seed in DEFAULT_ARTWORK_KINDS}
        assert {"cover_art", "unknown"} <= codes

    def test_unknown_expects_no_shape(self):
        """It is the absence of a claim about the artwork, not a claim about its shape.
        A constraint here would refuse the very rows it exists to hold."""
        unknown = next(s for s in DEFAULT_ARTWORK_KINDS if s.code == "unknown")
        assert (unknown.target_ratio, unknown.min_width, unknown.max_width) == (None, None, None)

    def test_thumbnail_expects_no_ratio(self):
        """It holds both 16:9 and 4:3 real rows. No tolerance admitting 1.333 alongside
        1.778 would mean anything, so width alone constrains it."""
        thumbnail = next(s for s in DEFAULT_ARTWORK_KINDS if s.code == "thumbnail")
        assert thumbnail.target_ratio is None
        assert thumbnail.min_width == 320

    def test_the_cover_art_tolerance_admits_the_row_it_exists_for(self):
        """499x500 is a real production cover, 0.2% off square. A zero-tolerance rule
        would misfile it, which is the whole argument for the column."""
        cover = next(s for s in DEFAULT_ARTWORK_KINDS if s.code == "cover_art")
        assert cover.target_ratio is not None and cover.ratio_tolerance is not None
        deviation = abs((499 / 500) - cover.target_ratio) / cover.target_ratio
        assert deviation <= cover.ratio_tolerance

    def test_the_poster_tolerance_admits_theatrical_art(self):
        """27:40 (0.675) is a legitimate poster shape, not only the 2:3 of the common
        sizes, so a tolerance tight enough to exclude it would be wrong."""
        poster = next(s for s in DEFAULT_ARTWORK_KINDS if s.code == "poster")
        assert poster.target_ratio is not None and poster.ratio_tolerance is not None
        deviation = abs((27 / 40) - poster.target_ratio) / poster.target_ratio
        assert deviation <= poster.ratio_tolerance

    def test_the_thumbnail_floor_excludes_the_row_that_justifies_it(self):
        """128x96 is the one stored row too small to be useful artwork of any kind."""
        thumbnail = next(s for s in DEFAULT_ARTWORK_KINDS if s.code == "thumbnail")
        assert thumbnail.min_width is not None and thumbnail.min_width > 128

    @pytest.mark.parametrize("code", ["poster", "backdrop", "still", "cover_art"])
    def test_a_kind_with_a_target_also_carries_a_tolerance(self, code):
        """An exact-match ratio rule refuses real files; every target needs a tolerance."""
        seed = next(s for s in DEFAULT_ARTWORK_KINDS if s.code == code)
        assert seed.target_ratio is not None
        assert seed.ratio_tolerance is not None and seed.ratio_tolerance > 0
