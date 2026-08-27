# tests/contracts/repositories/test_steam_repository_contract.py
import pytest

from app.repositories.errors import (
    ForeignKeyViolation,
    NotNullViolation,
)
from app.schemas import StreamListParams, StreamUpdateInternal
from tests.contracts.repositories.bundles_impl import make_bundle, stream_bundler
from tests.factories import AssetCreateFactory, StreamCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, stream_bundler)
    try:
        yield b
    finally:
        b.close()


def _all_streams(bundle, **params):
    """Every stream the repository will hand back, followed across pages.

    ``list_paged`` caps a response at ``limit``, so a test that wants "all of them"
    has to walk the cursors rather than read one page.

    Terminates on a null cursor. When this was written sqlakeyset's marker was
    serialised unconditionally, so ``next`` was never null and the walk also had to
    stop on an empty page or a cursor that stopped advancing; #66 made ``next`` null
    exactly when there is no further page, so those conditions are unreachable.

    Args:
        bundle: The repository bundle under test.
        **params: Overrides for ``StreamListParams`` (e.g. ``asset_id``, ``limit``).

    Returns:
        list[StreamRead]: Every row across every page, in cursor order.

    Raises:
        AssertionError: If the walk does not terminate within the cap. That guards
            against a regression in the #66 contract rather than against expected
            behaviour -- without it such a regression hangs the suite.
    """
    items = []
    cursor = None

    for _ in range(1000):
        page = bundle.streams.list_paged(StreamListParams(after=cursor, **params))
        items.extend(page.items)

        cursor = page.page.next
        if cursor is None:
            return items

    raise AssertionError("Exceeded max pagination steps; cursor likely not advancing.")


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
def test_list_paged(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    for i in range(10):
        bundle.streams.create(StreamCreateFactory(asset_id=asset.id))
    assert len(_all_streams(bundle)) == 10


@pytest.mark.contract
def test_list_paged_caps_the_response(bundle):
    """A page never exceeds `limit`, however many rows exist."""
    asset = bundle.assets.create(AssetCreateFactory())
    for _ in range(10):
        bundle.streams.create(StreamCreateFactory(asset_id=asset.id))

    page = bundle.streams.list_paged(StreamListParams(limit=4))

    assert len(page.items) == 4
    assert page.page.next is not None
    # and the cursor still reaches every row
    assert len(_all_streams(bundle, limit=4)) == 10


@pytest.mark.contract
def test_list_paged_filters_by_asset(bundle):
    """asset_id scopes the listing to one asset without needing a separate route."""
    asset_1 = bundle.assets.create(AssetCreateFactory())
    asset_2 = bundle.assets.create(AssetCreateFactory())
    for _ in range(5):
        bundle.streams.create(StreamCreateFactory(asset_id=asset_1.id))
    for _ in range(7):
        bundle.streams.create(StreamCreateFactory(asset_id=asset_2.id))

    assert len(_all_streams(bundle, asset_id=asset_1.id)) == 5
    assert len(_all_streams(bundle, asset_id=asset_2.id)) == 7
    assert len(_all_streams(bundle, asset_id=0)) == 0
    assert {s.asset_id for s in _all_streams(bundle, asset_id=asset_1.id)} == {asset_1.id}


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
    assert len(_all_streams(bundle)) == 5 + 7


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

    assert len(_all_streams(bundle)) == 10
    bundle.streams.delete_asset_streams(asset_1.id)
    assert len(_all_streams(bundle)) == 5


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
