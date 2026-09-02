"""The fixture cases.

Each case is a function taking a :class:`~tools.design_fixtures.capture.CaseContext`
and writing one or more fixtures through it. Cases are numbered to match the brief
they were written from, and ``--only`` selects a subset by number.

Where the API can express the query, the fixture is a single API response. Where it
cannot -- "roots with no release year", "assets belonging to no title" -- the ids are
chosen in the database and each record is then fetched through the API, so the fixture
is still a real API response shape. Those cases produce a *directory of detail bodies*
rather than a list page, which the manifest states explicitly.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from tools.design_fixtures.capture import CaseContext, parsed
from tools.design_fixtures.selectors import Selectors

# Titles list params that match the API's own defaults, passed explicitly so a fixture
# records the query that produced it rather than depending on the defaults staying put.
DEFAULT_PAGE = {"limit": 50, "sort": "id:asc"}

# How many failed transform requests to probe for logs before settling for one with
# none. Deliberately a constant rather than --max-records: that flag sizes fixture
# sets, and raising it to capture more unplaced assets should not also multiply the
# probing this case does.
LOG_PROBE_LIMIT = 50


def _cap(ids: list[int], limit: int | None) -> list[int]:
    """Apply a record cap.

    Args:
        ids: The full, ordered id list.
        limit: Maximum to keep, or None for all of them.

    Returns:
        list[int]: The capped list.
    """
    return ids if limit is None else ids[:limit]


def _capped_note(total: int, taken: int, unit: str) -> str | None:
    """Describe a cap, if one was applied.

    Args:
        total: How many records matched in the database.
        taken: How many were captured.
        unit: What the records are, for the sentence.

    Returns:
        str | None: A note, or None if nothing was capped.
    """
    if taken >= total:
        return None
    return (
        f"Capped: the first {taken} of {total} matching {unit}, taken in id order so a "
        f"re-run reproduces the same set. This fixture is a sample, not the whole set."
    )


# --------------------------------------------------------------- library grid


def case_01_library_roots_page1(ctx: CaseContext) -> None:
    """First page of library roots, default sort, page size 50."""
    body = ctx.capture.capture(
        "01-library-roots-page1.json",
        "/api/titles/",
        description="First page of library roots, default sort, page size 50.",
        selection="library_root=true with the API's default sort (id:asc) and page size (50).",
        params={"library_root": True, **DEFAULT_PAGE},
    )
    doc = parsed(body)
    cursor = None
    if isinstance(doc, dict):
        cursor = (doc.get("page") or {}).get("next")
    ctx.totals["_cursor"] = cursor
    ctx.totals["_page1_body"] = body

    # The same page with the optional resources a browse grid actually renders. The
    # brief names only sort and page size, so the specified request is captured above
    # unchanged; this is the superset, because without `display_image` every row's
    # image is null and the grid fixture would misrepresent what the grid shows.
    ctx.capture.capture(
        "01b-library-roots-page1-include.json",
        "/api/titles/",
        description=(
            "First page of library roots with the optional resources a grid renders "
            "(display_image, counts)."
        ),
        selection=(
            "Identical request to 01 plus include=display_image,counts. Captured "
            "alongside 01 rather than instead of it."
        ),
        params={"library_root": True, **DEFAULT_PAGE, "include": "display_image,counts"},
        note=(
            "Without include=display_image every row's display image is null, which is "
            "an artefact of the request rather than of the data."
        ),
    )


def case_02_library_roots_page2(ctx: CaseContext) -> None:
    """Second page of library roots, via the keyset cursor from page 1."""
    cursor = ctx.totals.get("_cursor")
    if not cursor:
        ctx.capture.record_finding(
            case="02",
            description="Second page of library roots via the keyset cursor.",
            selection="page.next from the page-1 response.",
            note="Page 1 returned no next cursor, so there is no second page.",
        )
        return
    ctx.capture.capture(
        "02-library-roots-page2.json",
        "/api/titles/",
        description="Second page of library roots, reached by the keyset cursor.",
        selection=(
            "The `page.next` cursor from fixture 01, passed as `after` with the same "
            "filter, sort and page size."
        ),
        params={"library_root": True, **DEFAULT_PAGE, "after": str(cursor)},
    )


def _count_roots(ctx: CaseContext, resolves_display_image: bool) -> int:
    """Count library roots on one side of the display-image split.

    The list endpoint is keyset-paginated and reports no total, so this walks it at the
    maximum page size. The walk is measurement, not capture -- nothing is written.

    Args:
        ctx: The running case context.
        resolves_display_image: Which side of the split to count.

    Returns:
        int: How many library roots match.
    """
    total = 0
    after: str | None = None
    while True:
        params = {
            "library_root": True,
            "resolves_display_image": resolves_display_image,
            "limit": 500,
            "sort": "id:asc",
            "after": after,
        }
        _, body = ctx.capture.get("/api/titles/", params)
        doc = parsed(body)
        if not isinstance(doc, dict):
            return total
        items = doc.get("items") or []
        total += len(items)
        after = (doc.get("page") or {}).get("next")
        if not after or not items:
            return total


def case_03_roots_no_display_image(ctx: CaseContext) -> None:
    """Library roots that resolve no display image -- the holes in the grid."""
    body = ctx.capture.capture(
        "03-library-roots-no-display-image.json",
        "/api/titles/",
        description="Library roots that resolve no display image.",
        selection=(
            "library_root=true&resolves_display_image=false. This asks whether "
            "include=display_image would resolve anything, including artwork borrowed "
            "from contents -- not whether the title carries artwork of its own."
        ),
        params={"library_root": True, "resolves_display_image": False, **DEFAULT_PAGE},
    )

    without = _count_roots(ctx, False)
    with_image = _count_roots(ctx, True)
    ctx.totals["library_roots_resolving_no_display_image"] = without
    ctx.totals["library_roots_resolving_a_display_image"] = with_image

    collection = without + with_image
    share = f"{(without / collection * 100):.0f}%" if collection else "n/a"
    note = (
        f"Measured across the whole collection: {without} of {collection} library roots "
        f"({share}) resolve no display image, and {with_image} resolve one. Holes are "
        f"the minority overall."
    )
    if body == ctx.totals.get("_page1_body"):
        note += (
            " But this fixture is byte-identical to 01-library-roots-page1.json, and "
            "that is the finding rather than a duplicate capture: under the default "
            "id:asc sort the first 50 library roots all resolve no display image, so "
            "filtering them out changes nothing on the first page. The holes are "
            "concentrated in the low id range, which means the default sort front-loads "
            "every one of them -- the first screen a designer sees is the worst case, "
            "not the typical one. The filter itself does work: "
            "resolves_display_image=true starts at a different title entirely."
        )
    # Attached to the fixture that was just written.
    ctx.capture.fixtures[-1] = replace(ctx.capture.fixtures[-1], note=note)


def case_04_roots_no_release_year(ctx: CaseContext) -> None:
    """Library roots with no release_year."""
    selectors: Selectors = ctx.selectors
    total = selectors.count(Selectors.COUNT_ROOTS_NO_RELEASE_YEAR)
    ctx.totals["roots_no_release_year"] = total
    if total == 0:
        ctx.capture.record_finding(
            case="04",
            description="Library roots with no release_year.",
            selection="titles.library_root AND release_year IS NULL.",
            note="No library root is missing a release year.",
        )
        return
    ids = _cap(selectors.ids(Selectors.ROOTS_NO_RELEASE_YEAR), ctx.max_records)
    ctx.capture.capture_each(
        "04-library-roots-no-release-year",
        "/api/titles/{id}",
        ids,
        description="A library root with no release_year",
        selection=(
            "The list endpoint has no filter for a missing release year, so ids came "
            "from `titles.library_root AND release_year IS NULL` in id order and each "
            "was fetched through GET /api/titles/{id}."
        ),
        note=(
            "Individual GET /api/titles/{id} bodies, not a list page. "
            + (_capped_note(total, len(ids), "library roots") or f"All {total} matches.")
        ),
    )


def case_05_roots_no_tags(ctx: CaseContext) -> None:
    """Library roots with no tags."""
    selectors: Selectors = ctx.selectors
    total = selectors.count(Selectors.COUNT_ROOTS_NO_TAGS)
    ctx.totals["roots_no_tags"] = total
    if total == 0:
        ctx.capture.record_finding(
            case="05",
            description="Library roots with no tags.",
            selection="titles.library_root with no row in title_tags.",
            note="Every library root carries at least one tag.",
        )
        return
    ids = _cap(selectors.ids(Selectors.ROOTS_NO_TAGS), ctx.max_records)
    ctx.capture.capture_each(
        "05-library-roots-no-tags",
        "/api/titles/{id}",
        ids,
        description="A library root with no tags",
        selection=(
            "`tag_ids` matches any-of and cannot express 'no tags', so ids came from "
            "library roots with no `title_tags` row, in id order, each fetched through "
            "GET /api/titles/{id}."
        ),
        note=(
            "Individual GET /api/titles/{id} bodies, not a list page. "
            + (_capped_note(total, len(ids), "library roots") or f"All {total} matches.")
        ),
    )


# -------------------------------------------------------------------- extremes


def case_06_longest_title_name(ctx: CaseContext) -> None:
    """The longest title name."""
    selectors: Selectors = ctx.selectors
    winner = selectors.measured(Selectors.LONGEST_TITLE_NAME)
    if winner is None:
        ctx.capture.record_finding(
            case="06",
            description="The title with the longest name.",
            selection="MAX(length(titles.name)).",
            note="There are no titles.",
        )
        return
    ctx.capture.capture(
        "06-longest-title-name.json",
        f"/api/titles/{winner.record_id}",
        description="The title with the longest name.",
        selection=(
            f"Measured: max length(name) across all titles = {winner.measure} "
            f"characters; ties break on lowest id."
        ),
    )


def case_07_synopsis_extremes(ctx: CaseContext) -> None:
    """The longest synopsis, and one with an empty synopsis."""
    selectors: Selectors = ctx.selectors
    longest = selectors.measured(Selectors.LONGEST_SYNOPSIS)
    if longest is None:
        ctx.capture.record_finding(
            case="07",
            description="The title with the longest synopsis.",
            selection="MAX(length(titles.synopsis)) over non-null synopses.",
            note="No title has a synopsis.",
        )
    else:
        ctx.capture.capture(
            "07-longest-synopsis.json",
            f"/api/titles/{longest.record_id}",
            description="The title with the longest synopsis.",
            selection=(
                f"Measured: max length(synopsis) = {longest.measure} characters; "
                f"ties break on lowest id."
            ),
        )

    empty_count = selectors.count(Selectors.COUNT_EMPTY_STRING_SYNOPSIS)
    null_count = selectors.count(Selectors.COUNT_NULL_SYNOPSIS)
    ctx.totals["synopsis_empty_string"] = empty_count
    ctx.totals["synopsis_null"] = null_count

    if empty_count > 0:
        winner = selectors.measured(Selectors.EMPTY_STRING_SYNOPSIS)
        assert winner is not None
        ctx.capture.capture(
            "07b-empty-synopsis.json",
            f"/api/titles/{winner.record_id}",
            description="A title whose synopsis is an empty string.",
            selection="Lowest id among titles where synopsis = ''.",
        )
        return

    if null_count == 0:
        ctx.capture.record_finding(
            case="07b",
            description="A title with an empty synopsis.",
            selection="synopsis = '' first, then synopsis IS NULL.",
            note="No title has an empty or absent synopsis.",
        )
        return

    winner = selectors.measured(Selectors.NULL_SYNOPSIS)
    assert winner is not None
    ctx.capture.capture(
        "07b-empty-synopsis.json",
        f"/api/titles/{winner.record_id}",
        description="A title with no synopsis.",
        selection="Lowest id among titles where synopsis IS NULL.",
        note=(
            f"Finding: there is no empty-*string* synopsis in this database -- 0 rows "
            f"have synopsis = '', while {null_count} have synopsis NULL. 'Empty' is "
            f"therefore null here, and a front end should expect null rather than ''."
        ),
    )


def case_08_asset_path_extremes(ctx: CaseContext) -> None:
    """The longest asset path and the longest filename."""
    selectors: Selectors = ctx.selectors
    longest_path = selectors.measured(Selectors.LONGEST_ASSET_PATH)
    if longest_path is None:
        ctx.capture.record_finding(
            case="08",
            description="The asset with the longest path.",
            selection="MAX(length(assets.path)).",
            note="There are no assets.",
        )
        return
    ctx.capture.capture(
        "08-longest-asset-path.json",
        f"/api/assets/{longest_path.record_id}",
        description="The asset with the longest path.",
        selection=(
            f"Measured: max length(path) = {longest_path.measure} characters; "
            f"ties break on lowest id."
        ),
    )
    longest_name = selectors.measured(Selectors.LONGEST_ASSET_FILENAME)
    assert longest_name is not None
    ctx.capture.capture(
        "08b-longest-asset-filename.json",
        f"/api/assets/{longest_name.record_id}",
        description="The asset with the longest filename.",
        selection=(
            f"Measured: max length(filename) = {longest_name.measure} characters; "
            f"ties break on lowest id."
        ),
    )


def case_09_most_children(ctx: CaseContext) -> None:
    """The parent with the most children, with its full contents listing."""
    selectors: Selectors = ctx.selectors
    winner = selectors.measured(Selectors.MOST_CHILD_TITLES)
    if winner is None:
        ctx.capture.record_finding(
            case="09",
            description="The parent title with the most child titles.",
            selection="MAX(count) over title_contents rows with a child_title_id.",
            note="No title contains another title.",
        )
        return
    ctx.capture.capture(
        "09-most-children-title.json",
        f"/api/titles/{winner.record_id}",
        description="The parent title with the most child titles.",
        selection=(
            f"Measured: {winner.measure} child titles, the most of any parent; "
            f"ties break on lowest id."
        ),
    )
    ctx.capture.capture(
        "09b-most-children-contents.json",
        f"/api/titles/{winner.record_id}/contents",
        description="That parent's full contents listing.",
        selection="GET /api/titles/{id}/contents for the same title.",
        note="This endpoint is unpaginated, so the fixture is the complete listing.",
    )


def case_10_deepest_chain(ctx: CaseContext) -> None:
    """The deepest intrinsic containment chain, every title along it."""
    selectors: Selectors = ctx.selectors
    chain = selectors.deepest_intrinsic_chain()
    if len(chain) < 2:
        ctx.capture.record_finding(
            case="10",
            description="The deepest intrinsic containment chain.",
            selection="Recursive walk of title_contents where membership='intrinsic'.",
            note=(
                "No intrinsic containment chain longer than a single title exists, so "
                "there is no chain to capture."
            ),
        )
        return
    ctx.totals["deepest_chain_depth"] = len(chain)
    ctx.capture.capture_each(
        "10-deepest-intrinsic-chain",
        "/api/titles/{id}",
        chain,
        description="A title on the deepest intrinsic containment chain",
        selection=(
            f"Recursive walk of title_contents where membership='intrinsic', starting "
            f"from titles with no intrinsic parent. Deepest chain is {len(chain)} "
            f"levels; ties break on the lowest id path."
        ),
        note=(
            f"Files are ordered root-first, one per level, {len(chain)} levels deep. "
            f"Each is an individual GET /api/titles/{{id}} body."
        ),
    )


def case_11_title_most_assets(ctx: CaseContext) -> None:
    """The title with the most assets attached."""
    selectors: Selectors = ctx.selectors
    winner = selectors.measured(Selectors.MOST_ASSETS)
    if winner is None:
        ctx.capture.record_finding(
            case="11",
            description="The title with the most assets attached.",
            selection="MAX(count) over title_contents rows with an asset_id.",
            note="No title has an asset attached.",
        )
        return
    ctx.capture.capture(
        "11-title-most-assets.json",
        f"/api/titles/{winner.record_id}",
        description="The title with the most assets attached.",
        selection=(
            f"Measured: {winner.measure} attached assets, the most of any title; "
            f"ties break on lowest id."
        ),
    )


def case_12_asset_most_streams(ctx: CaseContext) -> None:
    """The asset with the most streams."""
    selectors: Selectors = ctx.selectors
    winner = selectors.measured(Selectors.ASSET_MOST_STREAMS)
    if winner is None:
        ctx.capture.record_finding(
            case="12",
            description="The asset with the most streams.",
            selection="MAX(count) over streams grouped by asset_id.",
            note="No asset has any streams.",
        )
        return
    ctx.capture.capture(
        "12-asset-most-streams.json",
        f"/api/assets/{winner.record_id}",
        description="The asset with the most streams.",
        selection=(
            f"Measured: {winner.measure} streams, the most of any asset; ties break on "
            f"lowest id."
        ),
    )
    ctx.capture.capture(
        "12b-asset-most-streams-streams.json",
        f"/api/assets/{winner.record_id}/streams",
        description="That asset's streams.",
        selection="GET /api/assets/{id}/streams for the same asset.",
        note=(
            "Captured because the asset body carries no streams inline, so the "
            "measurement that chose this record is only visible here."
        ),
    )


# ------------------------------------------------------- detail and operations

TITLE_SUB_RESOURCES: list[tuple[str, str, str]] = [
    ("contents", "/api/titles/{id}/contents", "Its contents listing."),
    ("parents", "/api/titles/{id}/parents", "The titles that directly contain it."),
    ("artwork", "/api/titles/{id}/artwork", "Its artwork."),
    ("ids", "/api/titles/{id}/ids", "Its external identifiers."),
    ("references", "/api/titles/{id}/references", "Its references."),
]

ASSET_SUB_RESOURCES: list[tuple[str, str, str]] = [
    ("streams", "/api/assets/{id}/streams", "Its streams."),
    ("metadata", "/api/assets/{id}/metadata", "Its metadata."),
    ("ids", "/api/assets/{id}/ids", "Its external identifiers."),
    ("artwork", "/api/assets/{id}/artwork", "Its artwork."),
    ("derived-assets", "/api/assets/{id}/derived_assets", "Assets derived from it."),
    ("accessories", "/api/assets/{id}/accessories", "Its accessory files."),
    ("titles", "/api/assets/{id}/titles", "The titles it belongs to."),
    (
        "transform-requests",
        "/api/assets/{id}/transform_requests",
        "Transform requests against it.",
    ),
]

ACCESSORIES_NOTE = (
    "Finding: this endpoint lists files from the configured accessory_root on the "
    "machine running the API, not from the database. This capture ran with no media "
    "mounted, so an empty list here means 'no accessory directory on this machine' "
    "rather than 'this asset has no accessories in production'."
)


def case_13_title_full(ctx: CaseContext) -> None:
    """One title with its contents, parents, artwork, ids and references."""
    selectors: Selectors = ctx.selectors
    winner = selectors.measured(TITLE_COVERAGE)
    if winner is None:
        ctx.capture.record_finding(
            case="13",
            description="One title with every sub-resource populated.",
            selection="Coverage score over contents, parents, artwork, ids, references.",
            note="There are no titles.",
        )
        return
    title_id = winner.record_id
    title_artwork = selectors.count(Selectors.COUNT_TITLE_ARTWORK)
    ctx.totals["artwork_rows_on_titles"] = title_artwork
    selection = (
        f"Chosen by measurement: the title populating the most of the five "
        f"sub-resources ({winner.measure} of 5 non-empty); ties break on most contents, "
        f"then lowest id."
    )
    note: str | None = None
    if winner.measure < 5:
        note = (
            f"Finding: no title populates all five sub-resources. The best available "
            f"covers {winner.measure} of 5; the rest come back empty."
        )
    if title_artwork == 0:
        note = (f"{note} " if note else "") + (
            "Finding: not one title in this database carries artwork of its own — every "
            "artwork row belongs to an asset — so `artwork.json` here is empty for "
            "structural reasons, not because this title was a poor pick. A title still "
            "*shows* an image by borrowing one from its contents, which is what "
            "`include=display_image` resolves (see fixture 01b)."
        )
    ctx.capture.capture(
        "13-title-full/title.json",
        f"/api/titles/{title_id}",
        description="A title, with each of its sub-resources captured alongside.",
        selection=selection,
        note=note,
    )
    for name, template, description in TITLE_SUB_RESOURCES:
        ctx.capture.capture(
            f"13-title-full/{name}.json",
            template.format(id=title_id),
            description=description,
            selection=f"Sub-resource of the title in 13-title-full/title.json (id {title_id}).",
        )


def case_14_asset_full(ctx: CaseContext) -> None:
    """One asset with everything hanging off it."""
    selectors: Selectors = ctx.selectors
    winner = selectors.measured(ASSET_COVERAGE)
    if winner is None:
        ctx.capture.record_finding(
            case="14",
            description="One asset with every sub-resource populated.",
            selection="Coverage score over the seven database-backed sub-resources.",
            note="There are no assets.",
        )
        return
    asset_id = winner.record_id
    selection = (
        f"Chosen by measurement: the asset populating the most of its seven "
        f"database-backed sub-resources ({winner.measure} of 7 non-empty); ties break "
        f"on most streams, then lowest id."
    )
    ctx.capture.capture(
        "14-asset-full/asset.json",
        f"/api/assets/{asset_id}",
        description="An asset, with everything hanging off it captured alongside.",
        selection=selection,
        note=(
            None
            if winner.measure == 7
            else (
                f"Finding: no asset populates all seven database-backed sub-resources. "
                f"The best available covers {winner.measure} of 7; the rest come back "
                f"empty. Accessories are excluded from the score -- see below."
            )
        ),
    )
    for name, template, description in ASSET_SUB_RESOURCES:
        ctx.capture.capture(
            f"14-asset-full/{name}.json",
            template.format(id=asset_id),
            description=description,
            selection=f"Sub-resource of the asset in 14-asset-full/asset.json (id {asset_id}).",
            note=ACCESSORIES_NOTE if name == "accessories" else None,
        )


def case_15_failed_transform_request(ctx: CaseContext) -> None:
    """A transform request that failed, with its logs."""
    selectors: Selectors = ctx.selectors
    total = selectors.count(Selectors.COUNT_FAILED_TRANSFORM_REQUESTS)
    ctx.totals["failed_transform_requests"] = total
    if total == 0:
        ctx.capture.record_finding(
            case="15",
            description="A failed transform request with its logs.",
            selection="media_transform_requests.outcome = 'failed'.",
            note="No transform request has failed.",
        )
        return

    candidates = selectors.ids(Selectors.FAILED_TRANSFORM_REQUESTS)
    chosen: int | None = None
    log_body: bytes | None = None
    inspected = 0
    for request_id in candidates[:LOG_PROBE_LIMIT]:
        inspected += 1
        status, body = ctx.capture.get(f"/api/transform_requests/{request_id}/logs")
        doc = parsed(body)
        if status == 200 and isinstance(doc, list) and doc:
            chosen, log_body = request_id, body
            break

    if chosen is None:
        # Every failed request carries no logs. Capture the most recent one anyway --
        # the empty logs body is the shape the UI has to render -- and say so.
        chosen = candidates[0]
        ctx.capture.capture(
            "15-failed-transform-request/request.json",
            f"/api/transform_requests/{chosen}",
            description="A transform request that failed.",
            selection=f"Most recent of {total} requests with outcome='failed'.",
        )
        ctx.capture.capture(
            "15-failed-transform-request/logs.json",
            f"/api/transform_requests/{chosen}/logs",
            description="Its logs.",
            selection="GET /api/transform_requests/{id}/logs for the same request.",
            note=(
                f"Finding: none of the {inspected} most recent failed requests inspected "
                f"returned any log entries, so this logs body is empty. A failure with "
                f"no logs is the common case here, not an error in the capture."
            ),
        )
        return

    ctx.capture.capture(
        "15-failed-transform-request/request.json",
        f"/api/transform_requests/{chosen}",
        description="A transform request that failed.",
        selection=(
            f"The most recent of {total} requests with outcome='failed' that also has "
            f"log entries (inspected {inspected} in id-descending order)."
        ),
    )
    ctx.capture.capture(
        "15-failed-transform-request/logs.json",
        f"/api/transform_requests/{chosen}/logs",
        description="Its logs.",
        selection="GET /api/transform_requests/{id}/logs for the same request.",
    )
    assert log_body is not None


def case_16_transform_requests_page1(ctx: CaseContext) -> None:
    """First page of transform requests across the library."""
    ctx.capture.capture(
        "16-transform-requests-page1.json",
        "/api/transform_requests",
        description="First page of transform requests across the library.",
        selection="No filter, the API's default sort (id:asc) and page size (50).",
        params=DEFAULT_PAGE,
    )


# ------------------------------------------------------------ unplaced material


def case_17_assets_no_title(ctx: CaseContext) -> None:
    """Assets belonging to no title."""
    selectors: Selectors = ctx.selectors
    total = selectors.count(Selectors.COUNT_ASSETS_WITH_NO_TITLE)
    ctx.totals["assets_with_no_title"] = total
    if total == 0:
        ctx.capture.record_finding(
            case="17",
            description="Assets belonging to no title.",
            selection="assets with no title_contents row referencing them.",
            note="Every asset belongs to at least one title.",
        )
        return
    ids = _cap(selectors.ids(Selectors.ASSETS_WITH_NO_TITLE), ctx.max_records)
    ctx.capture.capture_each(
        "17-assets-no-title",
        "/api/assets/{id}",
        ids,
        description="An asset belonging to no title",
        selection=(
            "The assets list has no filter for 'unplaced', so ids came from assets with "
            "no `title_contents` row referencing them, in id order, each fetched "
            "through GET /api/assets/{id}."
        ),
        note=(
            "Individual GET /api/assets/{id} bodies, not a list page. "
            + (_capped_note(total, len(ids), "assets") or f"All {total} matches.")
        ),
    )


def case_18_titles_no_intrinsic_parent(ctx: CaseContext) -> None:
    """Titles with no intrinsic parent that are not library_root."""
    selectors: Selectors = ctx.selectors
    total = selectors.count(Selectors.COUNT_TITLES_NO_INTRINSIC_PARENT_NOT_ROOT)
    ctx.totals["titles_no_intrinsic_parent_not_root"] = total
    if total == 0:
        ctx.capture.record_finding(
            case="18",
            description="Titles with no intrinsic parent that are not library_root.",
            selection=(
                "titles where NOT library_root and no title_contents row with "
                "membership='intrinsic' names them as child."
            ),
            note=("Every non-root title has an intrinsic parent, so this work queue is " "empty."),
        )
        return
    # Taken whole rather than capped. This is the Organise surface's work queue, where
    # the useful property is that the set is complete -- and at this size it is cheap.
    ids = _cap(selectors.ids(Selectors.TITLES_NO_INTRINSIC_PARENT_NOT_ROOT), None)
    ctx.capture.capture_each(
        "18-titles-no-intrinsic-parent",
        "/api/titles/{id}",
        ids,
        description="A non-root title with no intrinsic parent",
        selection=(
            "`membership=intrinsic` asks whether such an edge exists and cannot be "
            "negated, so ids came from non-root titles with no intrinsic parent edge, "
            "in id order, each fetched through GET /api/titles/{id}."
        ),
        note=(
            "Individual GET /api/titles/{id} bodies, not a list page. "
            + (_capped_note(total, len(ids), "titles") or f"All {total} matches.")
        ),
    )


# ------------------------------------------------------------- reference data


def case_19_reference_data(ctx: CaseContext) -> None:
    """All tags, all title types, all artwork kinds."""
    ctx.capture.capture(
        "19-tags.json",
        "/api/tags",
        description="All tags.",
        selection=(
            "limit=500, the endpoint's maximum, so the page holds every tag rather "
            "than the default 50."
        ),
        params={"limit": 500, "sort": "id:asc"},
    )
    ctx.capture.capture(
        "19b-title-types.json",
        "/api/title_types",
        description="All title types.",
        selection="The endpoint is unpaginated and returns the whole collection.",
    )
    ctx.capture.capture(
        "19c-artwork-kinds.json",
        "/api/artwork_kinds",
        description="All artwork kinds.",
        selection="The endpoint is unpaginated and returns the whole collection.",
    )


# Coverage scoring queries, kept next to the cases that use them.
TITLE_COVERAGE = """
    WITH scored AS (
        SELECT t.id AS title_id,
               (SELECT count(*) FROM title_contents tc
                 WHERE tc.parent_title_id = t.id) AS n_contents,
               (SELECT count(*) FROM title_contents tc
                 WHERE tc.child_title_id = t.id) AS n_parents,
               (SELECT count(*) FROM artwork a
                 WHERE a.entity_type = 'title' AND a.entity_id = t.id) AS n_artwork,
               (SELECT count(*) FROM external_identifiers e
                 WHERE e.entity_type = 'title' AND e.entity_id = t.id) AS n_ids,
               (SELECT count(*) FROM title_references r
                 WHERE r.title_id = t.id) AS n_refs
        FROM titles t
    )
    SELECT title_id,
           (n_contents > 0)::int + (n_parents > 0)::int + (n_artwork > 0)::int
             + (n_ids > 0)::int + (n_refs > 0)::int AS coverage
    FROM scored
    ORDER BY coverage DESC, n_contents DESC, title_id ASC
    LIMIT 1
"""

ASSET_COVERAGE = """
    WITH scored AS (
        SELECT a.id AS asset_id,
               (SELECT count(*) FROM streams s WHERE s.asset_id = a.id) AS n_streams,
               (SELECT count(*) FROM metadata m WHERE m.asset_id = a.id) AS n_metadata,
               (SELECT count(*) FROM external_identifiers e
                 WHERE e.entity_type = 'asset' AND e.entity_id = a.id) AS n_ids,
               (SELECT count(*) FROM artwork w
                 WHERE w.entity_type = 'asset' AND w.entity_id = a.id) AS n_artwork,
               (SELECT count(*) FROM assets d WHERE d.master_asset_id = a.id) AS n_derived,
               (SELECT count(*) FROM title_contents tc WHERE tc.asset_id = a.id) AS n_titles,
               (SELECT count(*) FROM media_transform_requests r
                 WHERE r.asset_id = a.id) AS n_transforms
        FROM assets a
    )
    SELECT asset_id,
           (n_streams > 0)::int + (n_metadata > 0)::int + (n_ids > 0)::int
             + (n_artwork > 0)::int + (n_derived > 0)::int + (n_titles > 0)::int
             + (n_transforms > 0)::int AS coverage
    FROM scored
    ORDER BY coverage DESC, n_streams DESC, asset_id ASC
    LIMIT 1
"""


CASES: list[tuple[int, str, Callable[[CaseContext], None]]] = [
    (1, "Library grid: first page of roots", case_01_library_roots_page1),
    (2, "Library grid: second page via cursor", case_02_library_roots_page2),
    (3, "Library grid: roots resolving no display image", case_03_roots_no_display_image),
    (4, "Library grid: roots with no release_year", case_04_roots_no_release_year),
    (5, "Library grid: roots with no tags", case_05_roots_no_tags),
    (6, "Extreme: longest title name", case_06_longest_title_name),
    (7, "Extreme: longest and empty synopsis", case_07_synopsis_extremes),
    (8, "Extreme: longest asset path and filename", case_08_asset_path_extremes),
    (9, "Extreme: parent with the most children", case_09_most_children),
    (10, "Extreme: deepest intrinsic containment chain", case_10_deepest_chain),
    (11, "Extreme: title with the most assets", case_11_title_most_assets),
    (12, "Extreme: asset with the most streams", case_12_asset_most_streams),
    (13, "Detail: one title with every sub-resource", case_13_title_full),
    (14, "Detail: one asset with everything", case_14_asset_full),
    (15, "Operations: a failed transform request with logs", case_15_failed_transform_request),
    (16, "Operations: first page of transform requests", case_16_transform_requests_page1),
    (17, "Unplaced: assets belonging to no title", case_17_assets_no_title),
    (18, "Unplaced: non-root titles with no intrinsic parent", case_18_titles_no_intrinsic_parent),
    (19, "Reference data: tags, title types, artwork kinds", case_19_reference_data),
]
