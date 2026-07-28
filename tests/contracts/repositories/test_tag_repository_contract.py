# tests/contracts/repository/test_tag_repository_contract.py
from __future__ import annotations

from time import sleep

import pytest

from app.repositories.errors import ForeignKeyViolation, UniqueViolation
from app.schemas import TagListParams, TagUpdateInternal
from tests.contracts.repositories.bundles_impl import make_bundle, tag_bundler
from tests.factories import AssetCreateFactory, TagCreateFactory, TitleCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, tag_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):

    t = TagCreateFactory()
    out = bundle.tags.create(t)
    assert out.id is not None
    assert bundle.tags.exists(out.id) is True
    fetched = bundle.tags.get(out.id)
    assert fetched is not None
    assert fetched.name == t.name.lower()
    assert fetched.created_at is not None
    assert fetched.updated_at is not None
    fetched = bundle.tags.get_by_name(t.name)
    assert fetched is not None
    assert fetched.id == out.id
    assert fetched.name == t.name.lower()


@pytest.mark.contract
def test_create_with_invalid_parent(bundle):

    t = TagCreateFactory(parent_id=999)
    with pytest.raises(ForeignKeyViolation):
        bundle.tags.create(t)


@pytest.mark.contract
def test_create_duplicate(bundle):

    t = TagCreateFactory(name="test")
    bundle.tags.create(t)
    with pytest.raises(UniqueViolation):
        bundle.tags.create(TagCreateFactory(name="test"))


@pytest.mark.contract
def test_update(bundle):

    t = bundle.tags.create(TagCreateFactory())
    assert t and t.description is not None
    old_description = t.description
    initial_timestamp = t.updated_at
    sleep(1)  # to ensure the update timestamp check is valid
    updated = bundle.tags.update(t.id, TagUpdateInternal(description=f"updated: {old_description}"))  # type: ignore
    assert updated and updated.description is not None
    assert updated.description != old_description
    assert updated.updated_at > initial_timestamp


@pytest.mark.contract
def test_update_with_unset(bundle):

    t = bundle.tags.create(TagCreateFactory())
    assert t and t.description is not None
    updated = bundle.tags.update(t.id, TagUpdateInternal.model_validate({"description": None}))
    assert updated and updated.description is None


@pytest.mark.contract
def test_update_with_trivial_update(bundle):

    t = bundle.tags.create(TagCreateFactory())
    assert t and t.updated_at is not None and t.description is not None
    updated = bundle.tags.update(t.id, TagUpdateInternal.model_validate({}))
    assert updated and t.description is not None


@pytest.mark.contract
def test_update_with_invalid_parent(bundle):

    t = bundle.tags.create(TagCreateFactory(name="test"))
    with pytest.raises(ForeignKeyViolation):
        bundle.tags.update(t.id, TagUpdateInternal(parent_id=999))


@pytest.mark.contract
def test_list_tags(bundle):

    tags = [bundle.tags.create(TagCreateFactory(parent_id=None)) for _ in range(10)]
    ids = [tag.id for tag in tags]
    fetched_tags = bundle.tags.list_tags(parent_id=None)
    assert fetched_tags and len(fetched_tags) == 10
    assert all(tag.id in ids for tag in fetched_tags)

    child_tags = [bundle.tags.create(TagCreateFactory(parent_id=tags[0].id)) for _ in range(5)]
    child_tag_ids = [tag.id for tag in child_tags]

    # this should not have changed
    fetched_tags = bundle.tags.list_tags(parent_id=None)
    assert fetched_tags and len(fetched_tags) == 10
    assert all(tag.id in ids for tag in fetched_tags)

    fetched_child_tags = bundle.tags.list_tags(parent_id=tags[0].id)
    assert fetched_child_tags and len(fetched_child_tags) == 5
    assert all(tag.id in child_tag_ids for tag in fetched_child_tags)


@pytest.mark.contract
def test_list_paged_filter(bundle):

    for i in range(10):
        bundle.tags.create(TagCreateFactory(name=f"tag-{i}"))
    bundle.tags.create(TagCreateFactory(name="special-11"))
    paged = bundle.tags.list_paged(
        TagListParams(name="peci", limit=5, sort="name:asc"), parent_id=None
    )
    assert len(paged.items) >= 1


@pytest.mark.contract
def test_list_paged_no_filter(bundle):

    last_tag = bundle.tags.create(TagCreateFactory(name="ztag-11"))
    for i in range(10):
        bundle.tags.create(TagCreateFactory(name=f"tag-{i}", parent_id=last_tag.id))

    paged = bundle.tags.list_paged(TagListParams(limit=5, sort="name:asc"), parent_id=None)
    # no search term, so only the single root tag should be returned
    assert len(paged.items) == 1 and paged.items[0].name == last_tag.name


@pytest.mark.contract
def test_list_paged_one_root_with_filter(bundle):

    last_tag = bundle.tags.create(TagCreateFactory(name="ztag-11"))
    for i in range(10):
        bundle.tags.create(TagCreateFactory(name=f"tag-{i}", parent_id=last_tag.id))

    paged = bundle.tags.list_paged(
        TagListParams(name="tag", limit=5, sort="name:desc"), parent_id=None
    )
    # all tags should be returned here since no parent tag is specified but there is a search term
    assert len(paged.items) == 5 and paged.items[4].name < paged.items[0].name


@pytest.mark.contract
def test_list_paged_one_root_with_filter_under_parent(bundle):

    last_tag = bundle.tags.create(TagCreateFactory(name="ztag-11"))
    for i in range(10):
        bundle.tags.create(TagCreateFactory(name=f"tag-{i}", parent_id=last_tag.id))

    paged = bundle.tags.list_paged(
        TagListParams(name="tag", limit=5, sort="name:desc"), parent_id=last_tag.id
    )
    # only 10 of the tags should be returned here since the parent tag is specified with a search term
    assert len(paged.items) == 5 and paged.items[4].name < paged.items[0].name


@pytest.mark.contract
def test_tag_asset(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tag1 = bundle.tags.create(TagCreateFactory())
    tag2 = bundle.tags.create(TagCreateFactory())
    ids = [t.id for t in [tag1, tag2]]
    added_tags = bundle.tags.add_asset_tags(asset.id, [tag1.id])
    assert len(added_tags) == 1
    added_tags = bundle.tags.add_asset_tags(asset.id, [tag1.id, tag2.id])
    assert len(added_tags) == 1  # since the first tag is already associated with the asset
    tags = bundle.tags.get_asset_tags(asset_id=asset.id)
    assert tags and len(tags) == 2
    assert all(t.id in ids for t in tags)


@pytest.mark.contract
def test_tag_asset_with_invalid_tag(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    added_tags = bundle.tags.add_asset_tags(asset.id, [999])
    assert not added_tags


@pytest.mark.contract
def test_untag_asset(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tag1 = bundle.tags.create(TagCreateFactory())
    tag2 = bundle.tags.create(TagCreateFactory())
    ids = [t.id for t in [tag1, tag2]]
    added_tags = bundle.tags.add_asset_tags(asset.id, [tag1.id, tag2.id])
    assert len(added_tags) == 2
    tags = bundle.tags.get_asset_tags(asset_id=asset.id)
    assert tags and len(tags) == 2
    assert all(t.id in ids for t in tags)
    did_untag = bundle.tags.remove_asset_tag(asset.id, tag1.id)
    assert did_untag
    did_untag = bundle.tags.remove_asset_tag(asset.id, tag1.id)
    assert not did_untag  # since the asset has already been untagged
    tags = bundle.tags.get_asset_tags(asset_id=asset.id)
    assert tags and len(tags) == 1 and tags[0].id == tag2.id


@pytest.mark.contract
def test_untag_asset_with_invalid_tag(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    did_untag = bundle.tags.remove_asset_tag(asset.id, 999)
    assert not did_untag


@pytest.mark.contract
def test_untag_asset_with_untagged_tag(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tag = bundle.tags.create(TagCreateFactory())
    did_untag = bundle.tags.remove_asset_tag(asset.id, tag.id)
    assert not did_untag


@pytest.mark.contract
def test_untag_all_asset(bundle):

    asset = bundle.assets.create(AssetCreateFactory())
    tag1 = bundle.tags.create(TagCreateFactory())
    tag2 = bundle.tags.create(TagCreateFactory())
    ids = [t.id for t in [tag1, tag2]]
    added_tags = bundle.tags.add_asset_tags(asset.id, [tag1.id, tag2.id])
    assert len(added_tags) == 2
    tags = bundle.tags.get_asset_tags(asset_id=asset.id)
    assert tags and len(tags) == 2
    assert all(t.id in ids for t in tags)
    count = bundle.tags.remove_all_asset_tags(asset.id)
    assert count == 2
    tags = bundle.tags.get_asset_tags(asset_id=asset.id)
    assert not tags


@pytest.mark.contract
def test_tag_title(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    tag1 = bundle.tags.create(TagCreateFactory())
    tag2 = bundle.tags.create(TagCreateFactory())
    ids = [t.id for t in [tag1, tag2]]
    added_tags = bundle.tags.add_title_tags(title.id, [tag1.id])
    assert len(added_tags) == 1
    added_tags = bundle.tags.add_title_tags(title.id, [tag1.id, tag2.id])
    assert len(added_tags) == 1  # since the first tag is already associated with the title
    tags = bundle.tags.get_title_tags(title_id=title.id)
    assert tags and len(tags) == 2
    assert all(t.id in ids for t in tags)


@pytest.mark.contract
def test_tag_title_with_invalid_tag(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    added_tags = bundle.tags.add_title_tags(title.id, [999])
    assert not added_tags


@pytest.mark.contract
def test_untag_title(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    tag1 = bundle.tags.create(TagCreateFactory())
    tag2 = bundle.tags.create(TagCreateFactory())
    ids = [t.id for t in [tag1, tag2]]
    added_tags = bundle.tags.add_title_tags(title.id, [tag1.id, tag2.id])
    assert len(added_tags) == 2
    tags = bundle.tags.get_title_tags(title_id=title.id)
    assert tags and len(tags) == 2
    assert all(t.id in ids for t in tags)
    did_untag = bundle.tags.remove_title_tag(title.id, tag1.id)
    assert did_untag
    did_untag = bundle.tags.remove_title_tag(title.id, tag1.id)
    assert not did_untag  # since the title has already been untagged
    tags = bundle.tags.get_title_tags(title_id=title.id)
    assert tags and len(tags) == 1 and tags[0].id == tag2.id


@pytest.mark.contract
def test_untag_title_with_invalid_tag(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    did_untag = bundle.tags.remove_title_tag(title.id, 999)
    assert not did_untag


@pytest.mark.contract
def test_untag_title_with_untagged_tag(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    tag = bundle.tags.create(TagCreateFactory())
    did_untag = bundle.tags.remove_title_tag(title.id, tag.id)
    assert not did_untag


@pytest.mark.contract
def test_untag_all_title(bundle):

    title = bundle.titles.create(TitleCreateFactory())
    tag1 = bundle.tags.create(TagCreateFactory())
    tag2 = bundle.tags.create(TagCreateFactory())
    ids = [t.id for t in [tag1, tag2]]
    added_tags = bundle.tags.add_title_tags(title.id, [tag1.id, tag2.id])
    assert len(added_tags) == 2
    tags = bundle.tags.get_title_tags(title_id=title.id)
    assert tags and len(tags) == 2
    assert all(t.id in ids for t in tags)
    count = bundle.tags.remove_all_title_tags(title.id)
    assert count == 2
    tags = bundle.tags.get_title_tags(title_id=title.id)
    assert not tags


@pytest.mark.contract
def test_tag_stats(bundle):

    tag = bundle.tags.create(TagCreateFactory())
    titles = [bundle.titles.create(TitleCreateFactory()) for _ in range(10)]
    for title in titles:
        bundle.tags.add_title_tags(title.id, [tag.id])
    assets = [bundle.assets.create(AssetCreateFactory()) for _ in range(17)]
    for asset in assets:
        bundle.tags.add_asset_tags(asset.id, [tag.id])
    stats = bundle.tags.get_tag_usage_stats(tag.id)
    assert stats and stats.tag_id == tag.id and stats.asset_count == 17 and stats.title_count == 10
