# tests/contracts/repositories/test_external_identifier_repository_contract.py
from __future__ import annotations

import pytest

from app.repositories.errors import UniqueViolation, NotFoundError, ForeignKeyViolation
from app.schemas import ExternalIdentifierCreateInternal, ExternalIdentifierUpdateInternal
from app.schemas.enums import EntityTypeEnum
from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    _sqlite_session,
    external_identifier_bundler,
)
from tests.contracts.repositories._bundles import ExternalIdentifierRepoBundle
from tests.factories import IdSchemeCreateFactory, AssetCreateFactory, TitleCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine) -> ExternalIdentifierRepoBundle:
    b = make_bundle(db_session, _test_engine, external_identifier_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_and_resolve_roundtrip_asset(bundle: ExternalIdentifierRepoBundle):
    """Test creating an external ID for an asset and resolving it."""
    asset = bundle.assets.create(AssetCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory(code="imdb", label="IMDb"))

    created = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme.id,
            external_id="tt1234567",
        )
    )

    assert created.id is not None
    assert created.entity_type == EntityTypeEnum.asset
    assert created.entity_id == asset.id
    assert created.scheme_id == scheme.id
    assert created.external_id == "tt1234567"
    assert created.created_at is not None

    # Resolve by scheme_id
    result = bundle.external_identifiers.resolve(scheme.id, "tt1234567")
    assert result is not None
    entity_type, entity_id = result
    assert entity_type == EntityTypeEnum.asset
    assert entity_id == asset.id

    # Resolve by code
    result_by_code = bundle.external_identifiers.resolve_by_code("imdb", "tt1234567")
    assert result_by_code is not None
    entity_type, entity_id, scheme_id = result_by_code
    assert entity_type == EntityTypeEnum.asset
    assert entity_id == asset.id
    assert scheme_id == scheme.id


@pytest.mark.contract
def test_create_and_resolve_roundtrip_title(bundle: ExternalIdentifierRepoBundle):
    """Test creating an external ID for a title and resolving it."""
    title = bundle.titles.create(TitleCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory(code="tmdb", label="TMDB"))

    created = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.title,
            entity_id=title.id,
            scheme_id=scheme.id,
            external_id="12345",
        )
    )

    assert created.entity_type == EntityTypeEnum.title
    assert created.entity_id == title.id

    # Resolve
    result = bundle.external_identifiers.resolve(scheme.id, "12345")
    assert result is not None
    entity_type, entity_id = result
    assert entity_type == EntityTypeEnum.title
    assert entity_id == title.id


@pytest.mark.contract
def test_resolve_nonexistent_returns_none(bundle: ExternalIdentifierRepoBundle):
    """Test that resolving a nonexistent external ID returns None."""
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory())
    result = bundle.external_identifiers.resolve(scheme.id, "nonexistent")
    assert result is None

    result_by_code = bundle.external_identifiers.resolve_by_code("nonexistent_scheme", "123")
    assert result_by_code is None


@pytest.mark.contract
def test_list_for_entity_empty(bundle: ExternalIdentifierRepoBundle):
    """Test listing external IDs for an entity with none."""
    asset = bundle.assets.create(AssetCreateFactory())
    ids = bundle.external_identifiers.list_for_entity(EntityTypeEnum.asset, asset.id)
    assert ids == []


@pytest.mark.contract
def test_list_for_entity_multiple(bundle: ExternalIdentifierRepoBundle):
    """Test listing multiple external IDs for an entity."""
    asset = bundle.assets.create(AssetCreateFactory())
    scheme1 = bundle.id_schemes.create(IdSchemeCreateFactory(code="imdb", label="IMDb"))
    scheme2 = bundle.id_schemes.create(IdSchemeCreateFactory(code="tmdb", label="TMDB"))

    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme1.id,
            external_id="tt123",
        )
    )
    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme2.id,
            external_id="456",
        )
    )

    ids = bundle.external_identifiers.list_for_entity(EntityTypeEnum.asset, asset.id)
    assert len(ids) == 2
    assert ids[0].scheme is not None
    assert ids[1].scheme is not None
    # Should be ordered by scheme label (IMDb before TMDB)
    assert ids[0].scheme.code == "imdb"
    assert ids[1].scheme.code == "tmdb"


@pytest.mark.contract
def test_unique_constraint_on_scheme_and_external_id(bundle: ExternalIdentifierRepoBundle):
    """Test that (scheme_id, external_id) must be unique across all entities."""
    asset = bundle.assets.create(AssetCreateFactory())
    title = bundle.titles.create(TitleCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory())

    # First insert: asset + external_id
    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme.id,
            external_id="duplicate_id",
        )
    )

    # Second insert: title + same external_id in same scheme should fail
    with pytest.raises(UniqueViolation):
        bundle.external_identifiers.create(
            ExternalIdentifierCreateInternal(
                entity_type=EntityTypeEnum.title,
                entity_id=title.id,
                scheme_id=scheme.id,
                external_id="duplicate_id",
            )
        )


@pytest.mark.contract
def test_foreign_key_violation_invalid_scheme(bundle: ExternalIdentifierRepoBundle):
    """Test that invalid scheme_id raises FK violation."""
    asset = bundle.assets.create(AssetCreateFactory())

    with pytest.raises(ForeignKeyViolation):
        bundle.external_identifiers.create(
            ExternalIdentifierCreateInternal(
                entity_type=EntityTypeEnum.asset,
                entity_id=asset.id,
                scheme_id=9999,  # Nonexistent scheme
                external_id="test",
            )
        )


@pytest.mark.contract
def test_get_by_id(bundle: ExternalIdentifierRepoBundle):
    """Test getting an external identifier by its ID."""
    asset = bundle.assets.create(AssetCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory())

    created = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme.id,
            external_id="test123",
        )
    )

    fetched = bundle.external_identifiers.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.external_id == "test123"

    # Nonexistent ID
    assert bundle.external_identifiers.get(9999) is None


@pytest.mark.contract
def test_update_external_id(bundle: ExternalIdentifierRepoBundle):
    """Test updating an external identifier."""
    asset = bundle.assets.create(AssetCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory())

    created = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme.id,
            external_id="original",
        )
    )

    updated = bundle.external_identifiers.update(
        created.id, ExternalIdentifierUpdateInternal(external_id="updated")
    )

    assert updated.id == created.id
    assert updated.external_id == "updated"
    assert updated.entity_id == asset.id


@pytest.mark.contract
def test_update_nonexistent_raises_not_found(bundle: ExternalIdentifierRepoBundle):
    """Test updating a nonexistent record raises NotFoundError."""
    with pytest.raises(NotFoundError):
        bundle.external_identifiers.update(
            9999, ExternalIdentifierUpdateInternal(external_id="test")
        )


@pytest.mark.contract
def test_update_uniqueness_violation(bundle: ExternalIdentifierRepoBundle):
    """Test that updating to a duplicate (scheme, external_id) raises UniqueViolation."""
    asset1 = bundle.assets.create(AssetCreateFactory())
    asset2 = bundle.assets.create(AssetCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory())

    rec1 = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset1.id,
            scheme_id=scheme.id,
            external_id="existing",
        )
    )
    rec2 = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset2.id,
            scheme_id=scheme.id,
            external_id="other",
        )
    )

    # Try to update rec2 to have the same external_id as rec1
    with pytest.raises(UniqueViolation):
        bundle.external_identifiers.update(
            rec2.id, ExternalIdentifierUpdateInternal(external_id="existing")
        )


@pytest.mark.contract
def test_delete_external_id(bundle: ExternalIdentifierRepoBundle):
    """Test deleting an external identifier."""
    asset = bundle.assets.create(AssetCreateFactory())
    scheme = bundle.id_schemes.create(IdSchemeCreateFactory())

    created = bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme.id,
            external_id="to_delete",
        )
    )

    # Confirm it exists
    assert len(bundle.external_identifiers.list_for_entity(EntityTypeEnum.asset, asset.id)) == 1

    # Delete it
    bundle.external_identifiers.delete(created.id)

    # Confirm it's gone
    assert len(bundle.external_identifiers.list_for_entity(EntityTypeEnum.asset, asset.id)) == 0
    assert bundle.external_identifiers.get(created.id) is None

    # Deleting again should be a no-op (no error)
    bundle.external_identifiers.delete(created.id)


@pytest.mark.contract
def test_multiple_entities_different_schemes(bundle: ExternalIdentifierRepoBundle):
    """Test that the same entity can have external IDs in multiple schemes."""
    asset = bundle.assets.create(AssetCreateFactory())
    scheme1 = bundle.id_schemes.create(IdSchemeCreateFactory(code="imdb"))
    scheme2 = bundle.id_schemes.create(IdSchemeCreateFactory(code="tmdb"))

    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme1.id,
            external_id="id1",
        )
    )
    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme2.id,
            external_id="id2",
        )
    )

    # Both should be listed
    ids = bundle.external_identifiers.list_for_entity(EntityTypeEnum.asset, asset.id)
    assert len(ids) == 2


@pytest.mark.contract
def test_assets_and_titles_can_have_same_external_id_in_different_schemes(
    bundle: ExternalIdentifierRepoBundle,
):
    """Test that assets and titles can share the same external_id value if in different schemes."""
    asset = bundle.assets.create(AssetCreateFactory())
    title = bundle.titles.create(TitleCreateFactory())
    scheme1 = bundle.id_schemes.create(IdSchemeCreateFactory(code="scheme1"))
    scheme2 = bundle.id_schemes.create(IdSchemeCreateFactory(code="scheme2"))

    # Asset in scheme1 with ID "123"
    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.asset,
            entity_id=asset.id,
            scheme_id=scheme1.id,
            external_id="123",
        )
    )

    # Title in scheme2 with ID "123" (different scheme, so allowed)
    bundle.external_identifiers.create(
        ExternalIdentifierCreateInternal(
            entity_type=EntityTypeEnum.title,
            entity_id=title.id,
            scheme_id=scheme2.id,
            external_id="123",
        )
    )

    # Both should resolve correctly
    asset_result = bundle.external_identifiers.resolve(scheme1.id, "123")
    assert asset_result == (EntityTypeEnum.asset, asset.id)

    title_result = bundle.external_identifiers.resolve(scheme2.id, "123")
    assert title_result == (EntityTypeEnum.title, title.id)
