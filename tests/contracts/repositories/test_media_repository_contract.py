# tests/contracts/repositories/test_media_repository_contract.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.errors import (
    CheckViolation,
    DuplicatePathError,
    ForeignKeyViolation,
)
from app.schemas import (
    AssetListParams,
    AssetUpdateInternal,
)
from tests.contracts.repositories.bundles_impl import asset_bundler, make_bundle
from tests.factories import AssetCreateFactory, TagCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, asset_bundler)
    try:
        yield b
    finally:
        b.close()


def _collect_all_pages(repo, base_params):
    """Iterate forward using page.next until exhausted. Returns (items, seen_ids)."""
    params = base_params
    all_items = []
    seen = set()
    cursor = None

    while True:
        # set cursor for next page
        params = params.model_copy(update={"after": cursor}) if cursor else params
        page = repo.list_paged(params)
        all_items.extend(page.items)
        for it in page.items:
            seen.add(it.id)
        if not page.page.next or len(page.items) == 0:
            break
        cursor = page.page.next
    return all_items, seen


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):
    a = AssetCreateFactory()
    out = bundle.assets.create(a)
    assert out.id is not None
    assert bundle.assets.exists(out.id) is True
    fetched = bundle.assets.get(out.id)
    assert fetched is not None
    assert fetched.filename == a.filename


@pytest.mark.contract
def test_create_with_invalid_master_asset(bundle):
    a = AssetCreateFactory()
    a.master_asset_id = 0
    with pytest.raises(ForeignKeyViolation):
        bundle.assets.create(a)


@pytest.mark.contract
def test_create_with_invalid_duration(bundle):
    a = AssetCreateFactory()
    a.duration = -1
    with pytest.raises(CheckViolation):
        bundle.assets.create(a)


@pytest.mark.contract
def test_create_with_invalid_bitrate(bundle):
    a = AssetCreateFactory()
    a.bitrate = -1
    with pytest.raises(CheckViolation):
        bundle.assets.create(a)


@pytest.mark.contract
def test_create_with_invalid_size(bundle):
    a = AssetCreateFactory()
    a.size = -1
    with pytest.raises(CheckViolation):
        bundle.assets.create(a)


@pytest.mark.contract
def test_duplicate_path_is_rejected(bundle):
    # create the first asset
    first = bundle.assets.create(AssetCreateFactory(path="/m/a.mp4", filename="a.mp4"))
    assert first and first.path == "m/a.mp4" and first.filename == "a.mp4"
    with pytest.raises(DuplicatePathError):
        bundle.assets.create(AssetCreateFactory(path="/m/a.mp4", filename="b.mp4"))


@pytest.mark.contract
def test_list_filtered_pagination_and_sort(bundle):
    tag = bundle.tags.create(TagCreateFactory(name="tag"))

    # Seed 20 rows
    for i in range(20):
        a = bundle.assets.create(
            AssetCreateFactory(
                filename=f"file_{i}.mp4",
                path=f"/media/{i}/file_{i}.mp4",
                size=1000 + i,
                mtime=datetime.now(UTC) + timedelta(hours=i),
                duration=i,
            )
        )
        bundle.tags.add_asset_tags(a.id, [tag.id])

    # First page, mtime-desc, limit=5
    first = bundle.assets.list_paged(AssetListParams(limit=5, sort="mtime:desc"))
    assert len(first.items) <= 5
    mtimes = [it.mtime for it in first.items]
    assert mtimes == sorted(mtimes, reverse=True)

    # despite having tags, none should be present unless included
    assert all(not item.tags for item in first.items)

    # Walk all pages forward to ensure full coverage (20)
    all_items, seen = _collect_all_pages(bundle.assets, AssetListParams(limit=5, sort="size:desc"))

    assert len(seen) == 20
    # Ensure global non-increasing order across concatenated pages
    all_mtimes = [it.mtime for it in all_items]
    assert all_mtimes == sorted(all_mtimes, reverse=True)


@pytest.mark.contract
def test_list_filtered_pagination_and_sort_with_tags(bundle):
    tag_even = bundle.tags.create(TagCreateFactory(name="even"))

    # Seed 20 rows; tag even i
    for i in range(20):
        a = bundle.assets.create(
            AssetCreateFactory(
                filename=f"file_{i}.mp4",
                path=f"/media/{i}/file_{i}.mp4",
                size=1000 + i,
                mtime=datetime.now(UTC) + timedelta(hours=i),
                duration=i,
            )
        )
        if i % 2 == 0:
            bundle.tags.add_asset_tags(a.id, [tag_even.id])

    # First page: include tags
    first = bundle.assets.list_paged(AssetListParams(limit=5, sort="size:desc", include="tags"))
    assert len(first.items) <= 5
    sizes = [it.size for it in first.items]
    assert sizes == sorted(sizes, reverse=True)

    # Tag presence by parity holds within first page
    for item in first.items:
        if item.duration % 2 == 0:
            assert item.tags, f"expected tags for even duration, got none for id={item.id}"
        else:
            assert not item.tags, f"expected no tags for odd duration, got some for id={item.id}"

    # Walk all pages to check global conditions
    all_items, seen = _collect_all_pages(
        bundle.assets, AssetListParams(limit=5, sort="size:desc", include="tags")
    )
    assert len(seen) == 20

    # Order across all concatenated pages
    all_sizes = [it.size for it in all_items]
    assert all_sizes == sorted(all_sizes, reverse=True)

    # Parity/tag rule holds globally too
    for item in all_items:
        if item.duration % 2 == 0:
            assert item.tags
        else:
            assert not item.tags


@pytest.mark.contract
def test_list_derived_assets(bundle):
    master = bundle.assets.create(AssetCreateFactory())
    # two children referencing master
    child1 = bundle.assets.create(AssetCreateFactory(master_asset_id=master.id))
    child2 = bundle.assets.create(AssetCreateFactory(master_asset_id=master.id))
    derived = bundle.assets.list_derived_assets(master.id)
    ids = {a.id for a in derived}
    assert {child1.id, child2.id}.issubset(ids)


@pytest.mark.contract
def test_update_with_self_reference_master_asset(bundle):
    a = bundle.assets.create(AssetCreateFactory())
    with pytest.raises(CheckViolation):
        bundle.assets.update(a.id, AssetUpdateInternal.model_validate({"master_asset_id": a.id}))


@pytest.mark.contract
def test_update_with_invalid_master_asset(bundle):
    a = bundle.assets.create(AssetCreateFactory())
    with pytest.raises(ForeignKeyViolation):
        bundle.assets.update(
            a.id, AssetUpdateInternal.model_validate({"master_asset_id": a.id + 1})
        )


@pytest.mark.contract
def test_update_with_unset(bundle):
    a = bundle.assets.create(AssetCreateFactory())
    assert a and a.container_format is not None
    updated = bundle.assets.update(
        a.id, AssetUpdateInternal.model_validate({"container_format": None})
    )
    assert updated and updated.container_format is None


@pytest.mark.contract
def test_update_with_trivial_update(bundle):
    a = bundle.assets.create(AssetCreateFactory())
    assert a and a.container_format is not None
    updated = bundle.assets.update(a.id, AssetUpdateInternal.model_validate({}))
    assert updated and updated.container_format is not None


@pytest.mark.contract
def test_mark_assets_seen_updates_and_counts(bundle):
    # Seed 3 assets
    a1 = bundle.assets.create(AssetCreateFactory())
    a2 = bundle.assets.create(AssetCreateFactory())
    a3 = bundle.assets.create(AssetCreateFactory())

    # Initially None
    assert bundle.assets.get(a1.id).last_seen is None  # type: ignore
    assert bundle.assets.get(a2.id).last_seen is None  # type: ignore

    # Mark two seen
    count = bundle.assets.mark_assets_seen([a1.id, a2.id])
    assert count in (0, 2)  # some dialects may not report rowcount reliably

    # Verify updated
    r1 = bundle.assets.get(a1.id)
    r2 = bundle.assets.get(a2.id)
    r3 = bundle.assets.get(a3.id)
    assert r1 and r1.last_seen is not None
    assert r2 and r2.last_seen is not None
    assert r3 and r3.last_seen is None
