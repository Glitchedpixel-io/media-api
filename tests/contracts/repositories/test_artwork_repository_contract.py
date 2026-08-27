# tests/contracts/repositories/test_artwork_repository_contract.py
"""Contract coverage for the artwork repositories.

The interesting behaviour here is not CRUD -- it is the two things the *database*
guarantees and the service must not have to: that at most one artwork per (entity,
kind) is primary, and that the same file cannot be registered twice against one
entity. Those are partial and composite unique indexes, so they are only real if a
test drives them against a live schema rather than a mock.
"""

from __future__ import annotations

import pytest

from app.repositories.errors import NotFoundError, UniqueViolation
from app.schemas import (
    ArtworkCreateInternal,
    ArtworkKindCreateInternal,
    ArtworkKindUpdateInternal,
    ArtworkUpdateInternal,
)
from app.schemas.enums import EntityTypeEnum
from tests.contracts.repositories.bundles_impl import artwork_bundler, make_bundle
from tests.factories import AssetCreateFactory, TitleCreateFactory

#: A valid content-addressed path, in the layout artwork_relative_path produces (#101).
PATH_A = "ab/12/" + "ab12" + "0" * 60 + ".jpg"
PATH_B = "cd/34/" + "cd34" + "0" * 60 + ".png"


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, artwork_bundler)
    try:
        yield b
    finally:
        b.close()


@pytest.fixture
def poster_kind(bundle) -> int:
    """The seeded ``poster`` kind.

    Looked up rather than created: `db_session` seeds `DEFAULT_ARTWORK_KINDS` the way
    the migration does, so creating one here would collide on the unique code -- and
    testing against the seed is what production actually runs on.
    """
    kind = bundle.artwork_kinds.get_by_code("poster")
    assert kind is not None
    return kind.id


@pytest.fixture
def backdrop_kind(bundle) -> int:
    kind = bundle.artwork_kinds.get_by_code("backdrop")
    assert kind is not None
    return kind.id


@pytest.fixture
def title_id(bundle) -> int:
    return bundle.titles.create(TitleCreateFactory()).id


def _artwork(
    kind_id: int,
    entity_id: int,
    *,
    entity_type: EntityTypeEnum = EntityTypeEnum.title,
    storage_path: str = PATH_A,
    is_primary: bool = False,
) -> ArtworkCreateInternal:
    return ArtworkCreateInternal(
        entity_type=entity_type,
        entity_id=entity_id,
        artwork_kind_id=kind_id,
        storage_path=storage_path,
        mime="image/jpeg",
        width=1000,
        height=1500,
        is_primary=is_primary,
        source_scheme_id=None,
        source_external_id=None,
        source_url=None,
    )


@pytest.mark.contract
def test_create_and_get_roundtrip(bundle, poster_kind, title_id):
    created = bundle.artwork.create(_artwork(poster_kind, title_id))
    assert created.id is not None

    fetched = bundle.artwork.get(created.id)
    assert fetched is not None
    assert fetched.storage_path == PATH_A
    assert fetched.entity_type == EntityTypeEnum.title
    assert fetched.entity_id == title_id
    # The kind is exposed by its code, not its id -- the same contract TitleORM has.
    assert fetched.artwork_kind == "poster"


@pytest.mark.contract
def test_get_returns_none_for_a_missing_id(bundle):
    assert bundle.artwork.get(999_999) is None


@pytest.mark.contract
def test_artwork_attaches_to_an_asset_as_well_as_a_title(bundle, poster_kind):
    """The whole point of the polymorphic key: covers already exist beside assets."""
    asset_id = bundle.assets.create(AssetCreateFactory()).id
    created = bundle.artwork.create(
        _artwork(poster_kind, asset_id, entity_type=EntityTypeEnum.asset)
    )
    assert created.entity_type == EntityTypeEnum.asset

    rows = bundle.artwork.list_for_entity(EntityTypeEnum.asset, asset_id)
    assert [r.id for r in rows] == [created.id]


@pytest.mark.contract
def test_an_asset_and_a_title_sharing_an_id_do_not_share_artwork(bundle, poster_kind):
    """entity_id is only meaningful alongside entity_type.

    Nothing stops a title and an asset carrying the same numeric id, so a scoped read
    that forgot entity_type would silently return the other entity's artwork.
    """
    title = bundle.titles.create(TitleCreateFactory())
    asset = bundle.assets.create(AssetCreateFactory())

    bundle.artwork.create(_artwork(poster_kind, title.id, storage_path=PATH_A))
    bundle.artwork.create(
        _artwork(poster_kind, asset.id, entity_type=EntityTypeEnum.asset, storage_path=PATH_B)
    )

    title_rows = bundle.artwork.list_for_entity(EntityTypeEnum.title, title.id)
    asset_rows = bundle.artwork.list_for_entity(EntityTypeEnum.asset, asset.id)

    assert [r.storage_path for r in title_rows] == [PATH_A]
    assert [r.storage_path for r in asset_rows] == [PATH_B]


@pytest.mark.contract
def test_list_for_entity_filters_by_kind(bundle, poster_kind, backdrop_kind, title_id):
    poster = bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A))
    backdrop = bundle.artwork.create(_artwork(backdrop_kind, title_id, storage_path=PATH_B))

    assert [
        r.id for r in bundle.artwork.list_for_entity(EntityTypeEnum.title, title_id, poster_kind)
    ] == [poster.id]
    assert [
        r.id for r in bundle.artwork.list_for_entity(EntityTypeEnum.title, title_id, backdrop_kind)
    ] == [backdrop.id]
    assert len(bundle.artwork.list_for_entity(EntityTypeEnum.title, title_id)) == 2


@pytest.mark.contract
def test_list_for_entity_puts_the_primary_first(bundle, poster_kind, title_id):
    """So a caller taking the head of the list gets the primary without asking."""
    bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A))
    primary = bundle.artwork.create(
        _artwork(poster_kind, title_id, storage_path=PATH_B, is_primary=True)
    )

    rows = bundle.artwork.list_for_entity(EntityTypeEnum.title, title_id)
    assert rows[0].id == primary.id


@pytest.mark.contract
def test_only_one_artwork_per_kind_may_be_primary(bundle, poster_kind, title_id):
    """uq_artwork_one_primary_per_kind, enforced by the database.

    A service that checked first and then wrote would lose this race exactly the way
    tag-by-name did in #46.
    """
    bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A, is_primary=True))
    with pytest.raises(UniqueViolation):
        bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_B, is_primary=True))


@pytest.mark.contract
def test_the_primary_constraint_is_per_kind_not_per_entity(
    bundle, poster_kind, backdrop_kind, title_id
):
    """One title may hold a primary poster and a primary backdrop at once."""
    bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A, is_primary=True))
    backdrop = bundle.artwork.create(
        _artwork(backdrop_kind, title_id, storage_path=PATH_B, is_primary=True)
    )
    assert backdrop.is_primary is True


@pytest.mark.contract
def test_any_number_of_non_primary_artworks_are_allowed(bundle, poster_kind, title_id):
    """The unique index is partial -- it constrains only the rows claiming the flag."""
    for i in range(3):
        bundle.artwork.create(
            _artwork(poster_kind, title_id, storage_path=f"aa/{i:02d}/{'a' * 64}.jpg")
        )
    assert bundle.artwork.count_for_entity(EntityTypeEnum.title, title_id) == 3


@pytest.mark.contract
def test_the_same_file_cannot_be_registered_twice_for_one_entity(
    bundle, poster_kind, backdrop_kind, title_id
):
    """uq_artwork_entity_storage_path.

    Content addressing means an identical file always yields an identical
    storage_path, so a double registration is reachable rather than theoretical.
    """
    bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A))
    with pytest.raises(UniqueViolation):
        bundle.artwork.create(_artwork(backdrop_kind, title_id, storage_path=PATH_A))


@pytest.mark.contract
def test_one_file_may_be_shared_by_two_entities(bundle, poster_kind):
    """The reason artwork is content-addressed at all: a season and its episodes
    share one poster, stored once and referenced twice."""
    season = bundle.titles.create(TitleCreateFactory())
    episode = bundle.titles.create(TitleCreateFactory())

    bundle.artwork.create(_artwork(poster_kind, season.id, storage_path=PATH_A))
    shared = bundle.artwork.create(_artwork(poster_kind, episode.id, storage_path=PATH_A))

    assert shared.storage_path == PATH_A


@pytest.mark.contract
def test_get_primary_returns_the_flagged_row(bundle, poster_kind, title_id):
    bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A))
    primary = bundle.artwork.create(
        _artwork(poster_kind, title_id, storage_path=PATH_B, is_primary=True)
    )

    found = bundle.artwork.get_primary(EntityTypeEnum.title, title_id, poster_kind)
    assert found is not None and found.id == primary.id


@pytest.mark.contract
def test_get_primary_returns_none_when_nothing_is_flagged(bundle, poster_kind, title_id):
    """ "This title has no poster" is an ordinary answer, not an error."""
    bundle.artwork.create(_artwork(poster_kind, title_id))
    assert bundle.artwork.get_primary(EntityTypeEnum.title, title_id, poster_kind) is None


@pytest.mark.contract
def test_set_primary_demotes_the_incumbent(bundle, poster_kind, title_id):
    """Both halves must land in one transaction, or the unique index rejects the
    window in which two rows claim the flag."""
    old = bundle.artwork.create(
        _artwork(poster_kind, title_id, storage_path=PATH_A, is_primary=True)
    )
    new = bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_B))

    promoted = bundle.artwork.set_primary(new.id)

    assert promoted.is_primary is True
    demoted = bundle.artwork.get(old.id)
    assert demoted is not None and demoted.is_primary is False


@pytest.mark.contract
def test_set_primary_is_idempotent(bundle, poster_kind, title_id):
    already = bundle.artwork.create(
        _artwork(poster_kind, title_id, storage_path=PATH_A, is_primary=True)
    )
    assert bundle.artwork.set_primary(already.id).is_primary is True


@pytest.mark.contract
def test_set_primary_does_not_touch_another_kind(bundle, poster_kind, backdrop_kind, title_id):
    backdrop = bundle.artwork.create(
        _artwork(backdrop_kind, title_id, storage_path=PATH_B, is_primary=True)
    )
    poster = bundle.artwork.create(_artwork(poster_kind, title_id, storage_path=PATH_A))

    bundle.artwork.set_primary(poster.id)

    untouched = bundle.artwork.get(backdrop.id)
    assert untouched is not None and untouched.is_primary is True


@pytest.mark.contract
def test_set_primary_raises_for_a_missing_id(bundle):
    with pytest.raises(NotFoundError):
        bundle.artwork.set_primary(999_999)


@pytest.mark.contract
def test_update_changes_fields(bundle, poster_kind, title_id):
    created = bundle.artwork.create(_artwork(poster_kind, title_id))
    updated = bundle.artwork.update(created.id, ArtworkUpdateInternal(width=2000, height=3000))
    assert (updated.width, updated.height) == (2000, 3000)
    assert updated.storage_path == PATH_A


@pytest.mark.contract
def test_update_raises_for_a_missing_id(bundle):
    with pytest.raises(NotFoundError):
        bundle.artwork.update(999_999, ArtworkUpdateInternal(width=10))


@pytest.mark.contract
def test_delete_removes_the_row(bundle, poster_kind, title_id):
    created = bundle.artwork.create(_artwork(poster_kind, title_id))
    bundle.artwork.delete(created.id)
    assert bundle.artwork.get(created.id) is None


@pytest.mark.contract
def test_delete_raises_for_a_missing_id(bundle):
    with pytest.raises(NotFoundError):
        bundle.artwork.delete(999_999)


@pytest.mark.contract
def test_kind_usage_count_tracks_referencing_artwork(bundle, poster_kind, title_id):
    """What turns a delete of an in-use kind into a 409 rather than a raw
    ForeignKeyViolation mapped to 422 -- the reasoning TitleTypeService records."""
    assert bundle.artwork_kinds.usage_count(poster_kind) == 0
    bundle.artwork.create(_artwork(poster_kind, title_id))
    assert bundle.artwork_kinds.usage_count(poster_kind) == 1


@pytest.mark.contract
def test_kind_lookup_by_code(bundle, poster_kind):
    found = bundle.artwork_kinds.get_by_code("poster")
    assert found is not None and found.id == poster_kind
    assert bundle.artwork_kinds.get_by_code("nonexistent") is None


@pytest.mark.contract
def test_kind_codes_are_unique(bundle, poster_kind):
    with pytest.raises(UniqueViolation):
        bundle.artwork_kinds.create(
            ArtworkKindCreateInternal(code="poster", label="Duplicate", description=None)
        )


@pytest.mark.contract
def test_kind_create_get_roundtrip(bundle):
    created = bundle.artwork_kinds.create(
        ArtworkKindCreateInternal(code="fanart", label="Fan Art", description="Community art")
    )
    fetched = bundle.artwork_kinds.get(created.id)
    assert fetched is not None
    assert (fetched.code, fetched.label) == ("fanart", "Fan Art")
    assert fetched.description == "Community art"


@pytest.mark.contract
def test_kind_get_returns_none_for_a_missing_id(bundle):
    assert bundle.artwork_kinds.get(999_999) is None


@pytest.mark.contract
def test_kind_exists(bundle, poster_kind):
    assert bundle.artwork_kinds.exists(poster_kind) is True
    assert bundle.artwork_kinds.exists(999_999) is False


@pytest.mark.contract
def test_kind_list_all_is_ordered_by_code(bundle, poster_kind, backdrop_kind):
    """Ordered so a kind picker renders the same way on every request -- unordered
    reads come back in whatever order Postgres finds convenient, which changes."""
    codes = [k.code for k in bundle.artwork_kinds.list_all()]
    assert codes == sorted(codes)
    assert {"poster", "backdrop"} <= set(codes)


@pytest.mark.contract
def test_kind_update_changes_fields(bundle, poster_kind):
    updated = bundle.artwork_kinds.update(poster_kind, ArtworkKindUpdateInternal(label="Cover Art"))
    assert updated.label == "Cover Art"
    # Untouched fields survive a partial update.
    assert updated.code == "poster"


@pytest.mark.contract
def test_kind_update_raises_for_a_missing_id(bundle):
    with pytest.raises(NotFoundError):
        bundle.artwork_kinds.update(999_999, ArtworkKindUpdateInternal(label="x"))


@pytest.mark.contract
def test_kind_delete_removes_an_unused_kind(bundle, poster_kind):
    bundle.artwork_kinds.delete(poster_kind)
    assert bundle.artwork_kinds.get(poster_kind) is None


@pytest.mark.contract
def test_kind_delete_raises_for_a_missing_id(bundle):
    with pytest.raises(NotFoundError):
        bundle.artwork_kinds.delete(999_999)
