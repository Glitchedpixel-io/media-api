# tests/contracts/repositories/test_title_content_repository_contract.py
import pytest

from app.repositories.errors import (
    CheckViolation,
    ForeignKeyViolation,
    NotFoundError,
    NotNullViolation,
    UniqueViolation,
)
from app.schemas import (
    TitleContentInsert,
    TitleContentUpdateInternal,
)
from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    title_content_bundler,
)
from tests.factories import AssetCreateFactory, TitleCreateFactory


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, title_content_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Happy path basics ---------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_and_list_with_children(bundle):

    parent = bundle.titles.create(TitleCreateFactory())
    child_title = bundle.titles.create(TitleCreateFactory())
    asset = bundle.assets.create(AssetCreateFactory())

    # Create one title-backed and one asset-backed content under the same parent
    c1 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate(
            {
                "kind": "title",
                "child_title_id": child_title.id,
                "asset_id": None,
                "label": "Child Title",
            }
        ),
        position="start",
    )
    assert c1 is not None and bundle.title_contents.exists(c1.id)

    c2 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate(
            {
                "kind": "asset",
                "asset_id": asset.id,
                "child_title_id": None,
                "label": "Asset",
            }
        ),
        position="end",
    )
    assert c2 is not None and bundle.title_contents.exists(c2.id)

    # List with eager children
    rows = bundle.title_contents.list_title_content(parent.id, include_children=True)
    assert [r.id for r in rows] == [c1.id, c2.id]
    # include_children returns related objects
    assert rows[0].child_title is not None and rows[0].child_title.id == child_title.id
    assert rows[1].asset is not None and rows[1].asset.id == asset.id


# --- Ordering (LexoRank-like) --------------------------------------------------


@pytest.mark.contract
def test_positioning_and_reorder(bundle):

    parent = bundle.titles.create(TitleCreateFactory())
    a1 = bundle.assets.create(AssetCreateFactory())
    a2 = bundle.assets.create(AssetCreateFactory())
    a3 = bundle.assets.create(AssetCreateFactory())

    r1 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a1.id}),
        position="end",
    )
    r2 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a2.id}),
        position="end",
    )
    r3 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a3.id}),
        position="end",
    )
    assert r1 and r2 and r3

    def ids():
        return [x.id for x in bundle.title_contents.list_title_content(parent.id)]

    assert ids() == [r1.id, r2.id, r3.id]

    # Move r3 to the start
    bundle.title_contents.reorder(parent.id, r3.id, position="start")
    assert ids() == [r3.id, r1.id, r2.id]

    # Move r1 after r2 (end)
    bundle.title_contents.reorder(parent.id, r1.id, after_id=r2.id)
    assert ids() == [r3.id, r2.id, r1.id]

    # Insert a new item between r3 and r2 using before_id
    a4 = bundle.assets.create(AssetCreateFactory())
    r4 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a4.id}),
        before_id=r2.id,
    )
    assert ids() == [r3.id, r4.id, r2.id, r1.id]


# --- Constraint and domain-specific errors ------------------------------------


@pytest.mark.contract
def test_fk_violations_on_create(bundle):

    # Invalid parent
    with pytest.raises(ForeignKeyViolation):
        bundle.title_contents.create_positioned(
            0, TitleContentInsert.model_validate({"kind": "asset", "asset_id": 1})
        )

    # Valid parent but invalid asset and title ids
    parent = bundle.titles.create(TitleCreateFactory())
    with pytest.raises(ForeignKeyViolation):
        bundle.title_contents.create_positioned(
            parent.id,
            TitleContentInsert.model_validate({"kind": "asset", "asset_id": 0}),
        )
    with pytest.raises(ForeignKeyViolation):
        bundle.title_contents.create_positioned(
            parent.id,
            TitleContentInsert.model_validate({"kind": "title", "child_title_id": 0}),
        )


@pytest.mark.contract
def test_check_and_notnull_violations(bundle):

    parent = bundle.titles.create(TitleCreateFactory())

    # Mismatch: kind asset but providing child_title_id
    with pytest.raises(CheckViolation):
        bundle.title_contents.create_positioned(
            parent.id,
            TitleContentInsert.model_validate(
                {"kind": "asset", "child_title_id": 123, "asset_id": None}
            ),
        )

    # Mismatch: kind title but providing asset_id
    with pytest.raises(CheckViolation):
        bundle.title_contents.create_positioned(
            parent.id,
            TitleContentInsert.model_validate(
                {"kind": "title", "asset_id": 456, "child_title_id": None}
            ),
        )

    # NotNull: order_key is required on raw create via update path
    # Here we perform an update to null the order_key to trigger NOT NULL
    a = bundle.assets.create(AssetCreateFactory())
    r = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a.id}),
    )
    assert r is not None
    with pytest.raises(NotNullViolation):
        bundle.title_contents.update(
            r.id, TitleContentUpdateInternal.model_validate({"order_key": None})
        )


@pytest.mark.contract
def test_unique_violations_and_not_found(bundle):

    parent = bundle.titles.create(TitleCreateFactory())
    a = bundle.assets.create(AssetCreateFactory())
    t_child = bundle.titles.create(TitleCreateFactory())

    r1 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a.id}),
    )
    assert r1 is not None

    # Duplicate same asset under same parent -> unique violation
    with pytest.raises(UniqueViolation):
        bundle.title_contents.create_positioned(
            parent.id,
            TitleContentInsert.model_validate({"kind": "asset", "asset_id": a.id}),
        )

    # Duplicate same child title under same parent -> unique violation
    r2 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "title", "child_title_id": t_child.id}),
    )
    assert r2 is not None
    with pytest.raises(UniqueViolation):
        bundle.title_contents.create_positioned(
            parent.id,
            TitleContentInsert.model_validate({"kind": "title", "child_title_id": t_child.id}),
        )

    # NotFound on update non-existent id
    with pytest.raises(NotFoundError):
        bundle.title_contents.update(0, TitleContentUpdateInternal.model_validate({"label": "x"}))


@pytest.mark.contract
def test_update_parent_and_fk_violations(bundle):

    p1 = bundle.titles.create(TitleCreateFactory())
    p2 = bundle.titles.create(TitleCreateFactory())
    a = bundle.assets.create(AssetCreateFactory())

    r = bundle.title_contents.create_positioned(
        p1.id, TitleContentInsert.model_validate({"kind": "asset", "asset_id": a.id})
    )
    assert r is not None

    # Move to a different parent via update
    updated = bundle.title_contents.update(
        r.id, TitleContentUpdateInternal.model_validate({"parent_title_id": p2.id})
    )
    assert updated.parent_title_id == p2.id

    # Set non-existent parent -> FK violation
    with pytest.raises(ForeignKeyViolation):
        bundle.title_contents.update(
            r.id, TitleContentUpdateInternal.model_validate({"parent_title_id": 999999})
        )


@pytest.mark.contract
def test_delete(bundle):

    parent = bundle.titles.create(TitleCreateFactory())
    a = bundle.assets.create(AssetCreateFactory())

    r = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a.id}),
    )
    assert r is not None and bundle.title_contents.exists(r.id)

    bundle.title_contents.delete_title_content(r.id)
    assert not bundle.title_contents.exists(r.id)


# --- Get titles with asset ----------------------------------------------------


@pytest.mark.contract
def test_get_titles_with_asset_happy_path(bundle):

    # Create multiple titles with the same asset
    asset = bundle.assets.create(AssetCreateFactory())
    title_b = bundle.titles.create(TitleCreateFactory(name="Title B"))
    title_a = bundle.titles.create(TitleCreateFactory(name="Title A"))
    title_c = bundle.titles.create(TitleCreateFactory(name="Title C"))

    # Add the asset to each title
    bundle.title_contents.create_positioned(
        title_b.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": asset.id}),
    )
    bundle.title_contents.create_positioned(
        title_a.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": asset.id}),
    )
    bundle.title_contents.create_positioned(
        title_c.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": asset.id}),
    )

    # Get titles for the asset
    results = bundle.title_contents.get_titles_with_asset(asset.id)

    # Verify results are sorted by title name
    assert len(results) == 3
    assert results[0].parent_title.name == "Title A"
    assert results[1].parent_title.name == "Title B"
    assert results[2].parent_title.name == "Title C"
    assert all(r.asset_id == asset.id for r in results)


@pytest.mark.contract
def test_get_titles_with_asset_empty_list(bundle):

    # Create an asset that doesn't belong to any title
    asset = bundle.assets.create(AssetCreateFactory())

    # Get titles for the asset
    results = bundle.title_contents.get_titles_with_asset(asset.id)

    # Verify empty list is returned
    assert results == []
