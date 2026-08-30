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
        anchor="start",
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
        anchor="end",
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
        anchor="end",
    )
    r2 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a2.id}),
        anchor="end",
    )
    r3 = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a3.id}),
        anchor="end",
    )
    assert r1 and r2 and r3

    def ids():
        return [x.id for x in bundle.title_contents.list_title_content(parent.id)]

    assert ids() == [r1.id, r2.id, r3.id]

    # Move r3 to the start
    bundle.title_contents.reorder(parent.id, r3.id, anchor="start")
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


# --- Position invariants (#128) -----------------------------------------------
#
# `position` promises to be contiguous, zero-based and ascending within a parent. These
# assert that promise across the operations that can break it. The key-based scheme
# these replaced could not keep an equivalent promise: it ran out of room between two
# neighbours after about five front-insertions and rewrote the whole list to recover,
# and 7 of 60 randomised moves failed outright on its unique constraint.


def _positions(bundle, parent_id: int) -> list[int]:
    return [row.position for row in bundle.title_contents.list_title_content(parent_id)]


def _add(bundle, parent_id: int, **kwargs):
    asset = bundle.assets.create(AssetCreateFactory())
    return bundle.title_contents.create_positioned(
        parent_id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": asset.id}),
        **kwargs,
    )


@pytest.mark.contract
def test_repeated_insertion_at_the_front_never_runs_out_of_room(bundle):
    """Twelve front-insertions, where the old scheme exhausted its key space at five."""
    parent = bundle.titles.create(TitleCreateFactory())

    expected = []
    for _ in range(12):
        expected.insert(0, _add(bundle, parent.id, anchor="start").id)

    rows = bundle.title_contents.list_title_content(parent.id)
    assert [row.id for row in rows] == expected
    assert [row.position for row in rows] == list(range(12))


@pytest.mark.contract
def test_randomised_moves_keep_the_list_well_formed(bundle):
    """Sixty arbitrary moves, after each of which the list must still be a list.

    Seeded rather than property-based: the point is a fixed, reproducible sequence that
    exercises every anchor against a real database, not a search for new inputs.
    """
    import random

    rng = random.Random(11)
    parent = bundle.titles.create(TitleCreateFactory())
    for _ in range(10):
        _add(bundle, parent.id, anchor="end")

    for _ in range(60):
        rows = bundle.title_contents.list_title_content(parent.id)
        mover = rng.choice(rows)
        target = rng.choice([row for row in rows if row.id != mover.id])
        placement = rng.choice(
            [
                {"before_id": target.id},
                {"after_id": target.id},
                {"anchor": "start"},
                {"anchor": "end"},
            ]
        )
        bundle.title_contents.reorder(parent.id, mover.id, **placement)

        after = bundle.title_contents.list_title_content(parent.id)
        assert [row.position for row in after] == list(range(10))
        assert {row.id for row in after} == {row.id for row in rows}


@pytest.mark.contract
def test_deleting_an_entry_closes_the_gap(bundle):
    parent = bundle.titles.create(TitleCreateFactory())
    first, second, third = (_add(bundle, parent.id, anchor="end") for _ in range(3))

    bundle.title_contents.delete_title_content(second.id)

    rows = bundle.title_contents.list_title_content(parent.id)
    assert [row.id for row in rows] == [first.id, third.id]
    assert [row.position for row in rows] == [0, 1]


@pytest.mark.contract
def test_moving_an_entry_to_another_parent_renumbers_both(bundle):
    """The parent being left has to close its gap, not just the one being joined."""
    source = bundle.titles.create(TitleCreateFactory())
    destination = bundle.titles.create(TitleCreateFactory())
    first, second, third = (_add(bundle, source.id, anchor="end") for _ in range(3))
    resident = _add(bundle, destination.id, anchor="end")

    bundle.title_contents.reorder(destination.id, first.id, anchor="start")

    remaining = bundle.title_contents.list_title_content(source.id)
    assert [row.id for row in remaining] == [second.id, third.id]
    assert [row.position for row in remaining] == [0, 1]

    joined = bundle.title_contents.list_title_content(destination.id)
    assert [row.id for row in joined] == [first.id, resident.id]
    assert [row.position for row in joined] == [0, 1]


@pytest.mark.contract
def test_dropping_an_entry_onto_itself_leaves_it_alone(bundle):
    """A drag that ends where it started. The old scheme squeezed a new key in anyway."""
    parent = bundle.titles.create(TitleCreateFactory())
    first, second, third = (_add(bundle, parent.id, anchor="end") for _ in range(3))

    bundle.title_contents.reorder(parent.id, second.id, before_id=second.id)

    rows = bundle.title_contents.list_title_content(parent.id)
    assert [row.id for row in rows] == [first.id, second.id, third.id]
    assert [row.position for row in rows] == [0, 1, 2]


@pytest.mark.contract
def test_a_neighbour_from_another_parent_does_not_move_the_entry(bundle):
    """An id that is not in this list cannot say where in it to land."""
    parent = bundle.titles.create(TitleCreateFactory())
    elsewhere = bundle.titles.create(TitleCreateFactory())
    first, second = (_add(bundle, parent.id, anchor="end") for _ in range(2))
    stranger = _add(bundle, elsewhere.id, anchor="end")

    bundle.title_contents.reorder(parent.id, second.id, before_id=stranger.id)

    rows = bundle.title_contents.list_title_content(parent.id)
    assert [row.id for row in rows] == [first.id, second.id]
    assert [row.position for row in rows] == [0, 1]


@pytest.mark.contract
def test_patching_an_entry_to_another_parent_renumbers_both(bundle):
    """The patch path moves rows between parents too, and must keep both contiguous.

    `TitleContentService.update_title_content` sets `parent_title_id` from the URL on
    every patch, so this reaches the same place `reorder` does without going through it.
    """
    source = bundle.titles.create(TitleCreateFactory())
    destination = bundle.titles.create(TitleCreateFactory())
    first, second = (_add(bundle, source.id, anchor="end") for _ in range(2))
    resident = _add(bundle, destination.id, anchor="end")

    bundle.title_contents.update(
        first.id,
        TitleContentUpdateInternal.model_validate({"parent_title_id": destination.id}),
    )

    remaining = bundle.title_contents.list_title_content(source.id)
    assert [row.id for row in remaining] == [second.id]
    assert [row.position for row in remaining] == [0]

    joined = bundle.title_contents.list_title_content(destination.id)
    assert [row.id for row in joined] == [resident.id, first.id]
    assert [row.position for row in joined] == [0, 1]


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

    # NotNull: position is required, so nulling it through the update path raises
    a = bundle.assets.create(AssetCreateFactory())
    r = bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate({"kind": "asset", "asset_id": a.id}),
    )
    assert r is not None
    with pytest.raises(NotNullViolation):
        bundle.title_contents.update(
            r.id, TitleContentUpdateInternal.model_validate({"position": None})
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


# --- Aggregates (#96) ----------------------------------------------------------
#
# Two rules that differ deliberately, so each needs a fixture the other would pass:
#   counts  -- direct edges, every membership, no dedup needed
#   totals  -- recursive over intrinsic edges only, deduplicated by asset


def _contains_title(bundle, parent, child, membership="intrinsic"):
    return bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate(
            {
                "kind": "title",
                "child_title_id": child.id,
                "asset_id": None,
                "membership": membership,
            }
        ),
        anchor="end",
    )


def _contains_asset(bundle, parent, asset):
    return bundle.title_contents.create_positioned(
        parent.id,
        TitleContentInsert.model_validate(
            {"kind": "asset", "asset_id": asset.id, "child_title_id": None}
        ),
        anchor="end",
    )


@pytest.mark.contract
def test_counts_are_direct_edges_only(bundle):
    """A grandchild belongs to its own parent's count, not its grandparent's."""
    season = bundle.titles.create(TitleCreateFactory())
    episode = bundle.titles.create(TitleCreateFactory())
    asset = bundle.assets.create(AssetCreateFactory())
    _contains_title(bundle, season, episode)
    _contains_asset(bundle, episode, asset)

    counts = bundle.title_contents.counts_for_titles([season.id, episode.id])

    assert counts[season.id].child_count == 1
    assert counts[season.id].asset_count == 0, "the grandchild asset is not a direct edge"
    assert counts[episode.id].asset_count == 1


@pytest.mark.contract
def test_counts_include_curated_edges(bundle):
    """A curated list reports the things in it.

    This is the rule #96's text got wrong. Filtering to `membership = 'intrinsic'`
    would report every curated collection as containing nothing -- and the size of
    the list is the one number its tile exists to show.
    """
    curated_list = bundle.titles.create(TitleCreateFactory())
    for _ in range(3):
        member = bundle.titles.create(TitleCreateFactory())
        _contains_title(bundle, curated_list, member, membership="curated")

    counts = bundle.title_contents.counts_for_titles([curated_list.id])

    assert counts[curated_list.id].child_count == 3, "a curated list must report its real size"


@pytest.mark.contract
def test_a_child_under_two_parents_is_counted_by_both(bundle):
    """The fixture #96 asks for: one child, an intrinsic parent and a curated one.

    A fixture with one parent per child cannot distinguish a correct aggregate from a
    broken one -- both pass. This is the case that tells them apart.
    """
    home = bundle.titles.create(TitleCreateFactory())
    collection = bundle.titles.create(TitleCreateFactory())
    child = bundle.titles.create(TitleCreateFactory())
    _contains_title(bundle, home, child, membership="intrinsic")
    _contains_title(bundle, collection, child, membership="curated")

    counts = bundle.title_contents.counts_for_titles([home.id, collection.id])

    assert counts[home.id].child_count == 1
    assert counts[collection.id].child_count == 1


@pytest.mark.contract
def test_counts_omit_titles_that_contain_nothing(bundle):
    """An empty title is absent from the mapping; the service supplies the zero."""
    empty = bundle.titles.create(TitleCreateFactory())

    counts = bundle.title_contents.counts_for_titles([empty.id])

    assert empty.id not in counts


@pytest.mark.contract
def test_counts_of_nothing_issues_no_query(bundle):
    assert bundle.title_contents.counts_for_titles([]) == {}


@pytest.mark.contract
def test_totals_sum_assets_across_depth(bundle):
    """Runtime and size accumulate down the intrinsic tree."""
    season = bundle.titles.create(TitleCreateFactory())
    episode = bundle.titles.create(TitleCreateFactory())
    _contains_title(bundle, season, episode)
    _contains_asset(
        bundle, episode, bundle.assets.create(AssetCreateFactory(duration=90.0, size=700))
    )
    _contains_asset(
        bundle, season, bundle.assets.create(AssetCreateFactory(duration=10.0, size=300))
    )

    totals = bundle.title_contents.totals_for_titles([season.id])

    assert totals[season.id].total_runtime == 100.0
    assert totals[season.id].total_size == 1000


@pytest.mark.contract
def test_totals_count_a_shared_asset_once(bundle):
    """One asset under two titles in the same subtree contributes once.

    This is the deduplication that actually matters, and it is *not* the one #96
    describes. `uq_parent_asset_once` is scoped to a single parent, so the same file
    under two cuts is ordinary -- summing the join directly would double it.
    """
    season = bundle.titles.create(TitleCreateFactory())
    cut_a = bundle.titles.create(TitleCreateFactory())
    cut_b = bundle.titles.create(TitleCreateFactory())
    _contains_title(bundle, season, cut_a)
    _contains_title(bundle, season, cut_b)
    shared = bundle.assets.create(AssetCreateFactory(duration=50.0, size=500))
    _contains_asset(bundle, cut_a, shared)
    _contains_asset(bundle, cut_b, shared)

    totals = bundle.title_contents.totals_for_titles([season.id])

    assert totals[season.id].total_runtime == 50.0, "a shared asset must not be summed twice"
    assert totals[season.id].total_size == 500


@pytest.mark.contract
def test_totals_follow_intrinsic_edges_only(bundle):
    """A curated list does not absorb the runtime of what it borrows.

    The mirror of `test_counts_include_curated_edges`: the same edge is counted by
    `counts_for_titles` and ignored by `totals_for_titles`, which is the whole point
    of the two rules being different.
    """
    home = bundle.titles.create(TitleCreateFactory())
    collection = bundle.titles.create(TitleCreateFactory())
    child = bundle.titles.create(TitleCreateFactory())
    _contains_title(bundle, home, child, membership="intrinsic")
    _contains_title(bundle, collection, child, membership="curated")
    _contains_asset(bundle, child, bundle.assets.create(AssetCreateFactory(duration=42.0, size=99)))

    totals = bundle.title_contents.totals_for_titles([home.id, collection.id])
    counts = bundle.title_contents.counts_for_titles([collection.id])

    assert totals[home.id].total_runtime == 42.0, "the intrinsic parent owns the runtime"
    assert collection.id not in totals, "a curated edge contributes no runtime"
    assert counts[collection.id].child_count == 1, "but the same edge still counts as a child"


@pytest.mark.contract
def test_totals_omit_titles_with_nothing_beneath_them(bundle):
    bare = bundle.titles.create(TitleCreateFactory())

    assert bare.id not in bundle.title_contents.totals_for_titles([bare.id])


@pytest.mark.contract
def test_totals_of_nothing_issues_no_query(bundle):
    assert bundle.title_contents.totals_for_titles([]) == {}


@pytest.mark.contract
def test_totals_stop_at_the_depth_cap(bundle):
    """The walk is bounded, so a deep chain cannot cost unbounded recursion."""
    chain = [bundle.titles.create(TitleCreateFactory()) for _ in range(4)]
    for parent, child in zip(chain, chain[1:]):
        _contains_title(bundle, parent, child)
    _contains_asset(
        bundle, chain[-1], bundle.assets.create(AssetCreateFactory(duration=5.0, size=5))
    )

    reached = bundle.title_contents.totals_for_titles([chain[0].id], max_depth=8)
    stopped = bundle.title_contents.totals_for_titles([chain[0].id], max_depth=1)

    assert reached[chain[0].id].total_runtime == 5.0
    assert chain[0].id not in stopped, "the cap must stop the walk short of the asset"
