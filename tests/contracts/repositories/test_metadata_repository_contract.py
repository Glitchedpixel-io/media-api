# tests/contracts/repositories/test_metadata_repository_contract.py
import pytest

from app.repositories.errors import (
    ForeignKeyViolation,
    NotNullViolation,
)
from app.schemas import MetadataCreateInternal, MetadataUpdateInternal
from tests.contracts.repositories.bundles_impl import make_bundle, metadata_bundler
from tests.factories import AssetCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, metadata_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_roundtrip(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    m = MetadataCreateInternal.model_validate(
        {"asset_id": asset.id, "metadata_type": "probe", "data": {"x": 1}}
    )
    out = bundle.metadata.create(m)
    assert out.id is not None

    fetched = bundle.metadata.get(out.id)
    assert fetched is not None
    assert fetched.metadata_type == m.metadata_type
    assert fetched.asset_id == asset.id

    asset_meta = bundle.metadata.get_asset_metadata(asset.id)
    assert len(asset_meta) == 1
    assert asset_meta[0].id == out.id


@pytest.mark.contract
def test_create_with_invalid_asset_id(bundle):

    with pytest.raises(ForeignKeyViolation):
        bundle.metadata.create(
            MetadataCreateInternal.model_validate(
                {"asset_id": 999999, "metadata_type": "probe", "data": {}}
            )
        )


@pytest.mark.contract
def test_list_by_asset(bundle):

    asset_1 = bundle.assets.create(AssetCreateFactory())
    asset_2 = bundle.assets.create(AssetCreateFactory())

    for _ in range(5):
        bundle.metadata.create(
            MetadataCreateInternal.model_validate(
                {"asset_id": asset_1.id, "metadata_type": "probe", "data": {}}
            )
        )
    for _ in range(7):
        bundle.metadata.create(
            MetadataCreateInternal.model_validate(
                {"asset_id": asset_2.id, "metadata_type": "probe", "data": {}}
            )
        )

    a1 = bundle.metadata.get_asset_metadata(asset_1.id)
    a2 = bundle.metadata.get_asset_metadata(asset_2.id)
    a0 = bundle.metadata.get_asset_metadata(0)

    assert len(a1) == 5
    assert len(a2) == 7
    assert len(a0) == 0


@pytest.mark.contract
def test_delete(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    m1 = bundle.metadata.create(
        MetadataCreateInternal.model_validate(
            {"asset_id": asset.id, "metadata_type": "probe", "data": {}}
        )
    )
    m2 = bundle.metadata.create(
        MetadataCreateInternal.model_validate(
            {"asset_id": asset.id, "metadata_type": "probe", "data": {}}
        )
    )

    assert len(bundle.metadata.get_asset_metadata(asset.id)) == 2

    bundle.metadata.delete(m1.id)

    assert bundle.metadata.get(m1.id) is None
    remaining = bundle.metadata.get_asset_metadata(asset.id)
    assert len(remaining) == 1 and remaining[0].id == m2.id


@pytest.mark.contract
def test_update_metadata(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    m = bundle.metadata.create(
        MetadataCreateInternal.model_validate(
            {"asset_id": asset.id, "metadata_type": "ffprobe", "data": {"a": 1}}
        )
    )

    bundle.metadata.update(
        m.id,
        MetadataUpdateInternal.model_validate({"metadata_type": "scanner", "data": {"b": 2}}),
    )

    fetched = bundle.metadata.get(m.id)
    assert fetched.metadata_type == "scanner"
    assert fetched.data == {"b": 2}


@pytest.mark.contract
def test_update_with_null_fields(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    m = bundle.metadata.create(
        MetadataCreateInternal.model_validate(
            {"asset_id": asset.id, "metadata_type": "ffprobe", "data": {}}
        )
    )

    with pytest.raises(NotNullViolation):
        bundle.metadata.update(
            m.id,
            MetadataUpdateInternal.model_validate({"metadata_type": None}),
        )

    with pytest.raises(NotNullViolation):
        bundle.metadata.update(
            m.id,
            MetadataUpdateInternal.model_validate({"asset_id": None}),
        )


@pytest.mark.contract
def test_update_with_invalid_asset_id(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    m = bundle.metadata.create(
        MetadataCreateInternal.model_validate(
            {"asset_id": asset.id, "metadata_type": "ffprobe", "data": {}}
        )
    )

    with pytest.raises(ForeignKeyViolation):
        bundle.metadata.update(
            m.id,
            MetadataUpdateInternal.model_validate({"asset_id": 999999}),
        )
