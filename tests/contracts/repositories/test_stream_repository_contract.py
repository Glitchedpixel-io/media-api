# tests/contracts/repositories/test_steam_repository_contract.py
import pytest

from app.repositories.errors import (
    ForeignKeyViolation,
    NotNullViolation,
)
from app.schemas import StreamUpdateInternal
from tests.contracts.repositories.bundles_impl import make_bundle, stream_bundler
from tests.factories import AssetCreateFactory, StreamCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, stream_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):

    asset = bundle.assets.create(AssetCreateFactory())

    s = StreamCreateFactory(asset_id=asset.id)
    out = bundle.streams.create(s)
    assert out.id is not None
    assert bundle.streams.exists(out.id) is True
    fetched = bundle.streams.get(out.id)
    assert fetched is not None
    assert fetched.codec_type == s.codec_type


@pytest.mark.contract
def test_create_with_invalid_asset_id(bundle):

    with pytest.raises(ForeignKeyViolation):
        bundle.streams.create(StreamCreateFactory(asset_id=1))


@pytest.mark.contract
def test_list_all(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    for i in range(10):
        bundle.streams.create(StreamCreateFactory(asset_id=asset.id))
    assert len(bundle.streams.list_all()) == 10


@pytest.mark.contract
def test_list_by_asset(bundle):

    asset_1 = bundle.assets.create(AssetCreateFactory())
    asset_2 = bundle.assets.create(AssetCreateFactory())
    for i in range(5):
        bundle.streams.create(StreamCreateFactory(asset_id=asset_1.id))
    for i in range(7):
        bundle.streams.create(StreamCreateFactory(asset_id=asset_2.id))
    assert len(bundle.streams.get_asset_streams(asset_1.id)) == 5
    assert len(bundle.streams.get_asset_streams(asset_2.id)) == 7
    assert len(bundle.streams.get_asset_streams(0)) == 0
    assert len(bundle.streams.list_all()) == 5 + 7


@pytest.mark.contract
def test_delete(bundle):

    asset_1 = bundle.assets.create(AssetCreateFactory())
    asset_2 = bundle.assets.create(AssetCreateFactory())
    s = bundle.streams.create(StreamCreateFactory(asset_id=asset_1.id))
    assert bundle.streams.exists(s.id) is True
    bundle.streams.delete_asset_streams(asset_2.id)
    assert bundle.streams.exists(s.id) is True
    bundle.streams.delete_asset_streams(asset_1.id)
    assert bundle.streams.exists(s.id) is False

    for i in range(5):
        bundle.streams.create(StreamCreateFactory(asset_id=asset_1.id))
        bundle.streams.create(StreamCreateFactory(asset_id=asset_2.id))

    assert len(bundle.streams.list_all()) == 10
    bundle.streams.delete_asset_streams(asset_1.id)
    assert len(bundle.streams.list_all()) == 5


@pytest.mark.contract
def test_update_stream_metadata(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    s = bundle.streams.create(
        StreamCreateFactory(asset_id=asset.id, codec_name="aac", codec_type="audio")
    )
    assert s.codec_name == "aac" and s.codec_type == "audio"
    bundle.streams.update(
        s.id,
        StreamUpdateInternal.model_validate({"codec_name": "h265", "codec_type": "video"}),
    )
    fetched = bundle.streams.get(s.id)
    assert fetched.codec_name == "h265" and fetched.codec_type == "video"


@pytest.mark.contract
def test_update_stream_without_codec_type(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    s = bundle.streams.create(
        StreamCreateFactory(asset_id=asset.id, codec_name="aac", codec_type="audio")
    )
    assert s.codec_name == "aac" and s.codec_type == "audio"
    with pytest.raises(NotNullViolation):
        bundle.streams.update(
            s.id,
            StreamUpdateInternal.model_validate({"codec_name": "h265", "codec_type": None}),
        )


@pytest.mark.contract
def test_update_stream_without_asset_id(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    s = bundle.streams.create(
        StreamCreateFactory(asset_id=asset.id, codec_name="aac", codec_type="audio")
    )
    assert s.codec_name == "aac" and s.codec_type == "audio"
    with pytest.raises(NotNullViolation):
        bundle.streams.update(
            s.id,
            StreamUpdateInternal.model_validate({"asset_id": None}),
        )


@pytest.mark.contract
def test_update_stream_with_invalid_asset_id(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    s = bundle.streams.create(
        StreamCreateFactory(asset_id=asset.id, codec_name="aac", codec_type="audio")
    )
    assert s.codec_name == "aac" and s.codec_type == "audio"
    with pytest.raises(ForeignKeyViolation):
        bundle.streams.update(
            s.id,
            StreamUpdateInternal.model_validate({"asset_id": 999}),
        )
