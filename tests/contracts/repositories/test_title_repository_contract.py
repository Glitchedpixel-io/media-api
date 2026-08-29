# tests/contracts/repositories/test_title_repository_contract.py
from __future__ import annotations

import pytest

from app.repositories.errors import (
    EnumViolation,
    NotFoundError,
    NotNullViolation,
)
from app.schemas import TitleListParams, TitleUpdateInternal
from tests.contracts.repositories.bundles_impl import make_bundle, title_bundler
from tests.factories import (
    TagCreateFactory,
    TitleCreateFactory,
    TitleReferenceReadFactory,
)


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, title_bundler)
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

    t = TitleCreateFactory()
    out = bundle.titles.create(t)
    assert out.id is not None
    assert bundle.titles.exists(out.id) is True
    fetched = bundle.titles.get(out.id)
    assert fetched is not None
    assert fetched.name == t.name


@pytest.mark.contract
def test_list_filtered_pagination_and_sort(bundle):

    tag = bundle.tags.create(TagCreateFactory(name="tag"))

    # Seed 20 rows
    for i in range(20):
        t = bundle.titles.create(
            TitleCreateFactory(
                name=f"Title {i}",
            )
        )
        bundle.tags.add_title_tags(t.id, [tag.id])

    # First page, name, limit=5
    first = bundle.titles.list_paged(TitleListParams(limit=5, sort="name:desc"))
    assert len(first.items) <= 5
    names = [it.name for it in first.items]
    assert names == sorted(names, reverse=True)

    # despite having tags, none should be present unless included
    assert all(not item.tags for item in first.items)

    # Walk all pages forward to ensure full coverage (20)
    all_items, seen = _collect_all_pages(bundle.titles, TitleListParams(limit=5, sort="name:desc"))

    assert len(seen) == 20
    # Ensure global non-increasing order across concatenated pages
    all_names = [it.name for it in all_items]
    assert all_names == sorted(all_names, reverse=True)


@pytest.mark.contract
def test_list_paged_filter(bundle):

    for i in range(10):
        bundle.titles.create(TitleCreateFactory(name=f"Title {i}"))
    bundle.titles.create(TitleCreateFactory(name="Special 10"))
    title_list = bundle.titles.list_paged(
        TitleListParams(
            limit=5,
            sort="name:asc",
            name="peci",
        )
    )
    assert len(title_list.items) == 1


@pytest.mark.contract
def test_list_paged_filter_include(bundle):

    tag_even = bundle.tags.create(TagCreateFactory(name="even"))
    tag_odd = bundle.tags.create(TagCreateFactory(name="odd"))
    for i in range(10):
        t = bundle.titles.create(TitleCreateFactory(name=f"Title {i}"))
        r = TitleReferenceReadFactory(title_id=t.id)
        bundle.title_references.create(r)
        if i % 2 == 0:
            bundle.tags.add_title_tags(t.id, [tag_even.id])
        else:
            bundle.tags.add_title_tags(t.id, [tag_odd.id])

    # include references but not tags
    title_list = bundle.titles.list_paged(
        TitleListParams(
            limit=5,
            sort="name:asc",
            include="references",
        )
    )

    assert len(title_list.items) == 5  # and title_list.meta.total == 10
    assert all(title.references and len(title.references) == 1 for title in title_list.items)
    assert all(not title.tags for title in title_list.items)

    # include tags but not references
    title_list = bundle.titles.list_paged(
        TitleListParams(
            limit=5,
            sort="name:asc",
            include="tags",
        )
    )

    assert len(title_list.items) == 5  # and title_list.meta.total == 10
    assert all(title.tags and len(title.tags) == 1 for title in title_list.items)
    assert all(not title.references for title in title_list.items)

    # include both tags and references
    title_list = bundle.titles.list_paged(
        TitleListParams(
            limit=5,
            sort="name:asc",
            include=" tags, REferENces ",  # secretly tests that whitespace is stripped and keywords are lowercased
        )
    )

    assert len(title_list.items) == 5  # and title_list.meta.total == 10
    assert all(title.tags and len(title.tags) == 1 for title in title_list.items)
    assert all(title.references and len(title.references) == 1 for title in title_list.items)


@pytest.mark.contract
def test_list_paged_sorting_disallowed_field(bundle):

    for i in range(20):
        bundle.titles.create(TitleCreateFactory(name=f"Title {i}"))
    with pytest.raises(EnumViolation):
        bundle.titles.list_paged(
            TitleListParams(
                limit=11,
                sort="release_year:desc",
            )
        )


@pytest.mark.contract
def test_list_paged_sorting_by_title_type_is_alphabetical_by_code(bundle, title_type_ids):
    """Sorting on title_type orders by the type's code, not by its id.

    title_type is no longer a column on titles -- it lives on the joined
    title_types table, and TITLE_SORT reaches it through a field override. The
    three types here are chosen so that alphabetical order (audiobook, episode,
    movie) and seeded order (movie=1, episode=2, audiobook=4) disagree: sorting
    by the foreign key instead of the code would pass a two-type test but fails
    this one.
    """
    codes = ["movie", "episode", "audiobook"]
    for i in range(21):
        code = codes[i % len(codes)]
        bundle.titles.create(
            TitleCreateFactory(name=f"Title {i}", title_type_id=title_type_ids[code])
        )

    title_list = bundle.titles.list_paged(TitleListParams(limit=21, sort="title_type:asc"))
    assert [t.title_type for t in title_list.items] == ["audiobook"] * 7 + ["episode"] * 7 + [
        "movie"
    ] * 7

    title_list = bundle.titles.list_paged(TitleListParams(limit=21, sort="title_type:desc"))
    assert [t.title_type for t in title_list.items] == ["movie"] * 7 + ["episode"] * 7 + [
        "audiobook"
    ] * 7


@pytest.mark.contract
def test_allowed_updates(bundle):

    t = bundle.titles.create(TitleCreateFactory(name="Title 1"))
    assert t and t.name == "Title 1"
    updated = bundle.titles.update(
        t.id,
        TitleUpdateInternal.model_validate({"name": "New Title", "release_year": 2021}),
    )
    assert updated and updated.name == "New Title" and updated.release_year == 2021
    updated = bundle.titles.update(t.id, TitleUpdateInternal.model_validate({"name": "Title 2"}))
    assert (
        updated
        and updated.name == "Title 2"
        and updated.release_year == 2021
        and updated.synopsis is None
    )
    updated = bundle.titles.update(
        t.id,
        TitleUpdateInternal.model_validate(
            {"release_year": None, "synopsis": "A rather good movie."}
        ),
    )
    assert (
        updated
        and updated.name == "Title 2"
        and updated.release_year is None
        and updated.synopsis == "A rather good movie."
    )
    updated = bundle.titles.update(t.id, TitleUpdateInternal.model_validate({"synopsis": None}))
    assert (
        updated
        and updated.name == "Title 2"
        and updated.release_year is None
        and updated.synopsis is None
    )


@pytest.mark.contract
def test_invalid_updates(bundle):

    t = bundle.titles.create(TitleCreateFactory(name="Title 1"))
    assert t and t.name == "Title 1"
    with pytest.raises(NotNullViolation):
        bundle.titles.update(t.id, TitleUpdateInternal.model_validate({"title_type_id": None}))
    with pytest.raises(NotNullViolation):
        bundle.titles.update(t.id, TitleUpdateInternal.model_validate({"name": None}))
    with pytest.raises(NotFoundError):
        bundle.titles.update(0, TitleUpdateInternal.model_validate({"release_year": 1999}))
