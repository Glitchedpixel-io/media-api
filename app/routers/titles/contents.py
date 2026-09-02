# app/routers/titles/contents.py
from fastapi import APIRouter, Depends, Query

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_title_content_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    TitleContentBatchIds,
    TitleContentBatchInsert,
    TitleContentBatchResult,
    TitleContentInsert,
    TitleContentPatchPublic,
    TitleContentRead,
    TitleContentReadExtended,
    TitleContentReadParent,
)
from app.services import TitleContentService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{title_id}/parents",
    response_model=list[TitleContentReadParent],
    operation_id="list_title_parents",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of parent titles retrieved successfully"},
    },
)
def read_title_parents(
    title_id: int,
    service: TitleContentService = Depends(get_title_content_service),
) -> list[TitleContentReadParent]:
    """The titles that directly contain this one.

    The upward counterpart of `GET /api/assets/{asset_id}/titles`, and the same shape:
    each containment row with its parent, so a caller sees the label and order this
    title carries within that parent rather than only the parent's identity.

    Immediate parents only. Walking to the full ancestor set is a different question --
    an ancestor *set* and a breadcrumb *path* diverge as soon as a title has more than
    one parent, and what a breadcrumb should follow is #90's to settle.
    """
    return service.get_parents_of_title(title_id)


@router.get(
    "/{parent_title_id}/contents",
    response_model=list[TitleContentReadExtended],
    operation_id="list_title_contents",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of title contents retrieved successfully"},
    },
)
def read_title_contents(
    parent_title_id: int,
    service: TitleContentService = Depends(get_title_content_service),
) -> list[TitleContentReadExtended] | None:
    return service.get_title_content(parent_title_id)


@router.post(
    "/{parent_title_id}/contents",
    response_model=TitleContentRead,
    status_code=201,
    operation_id="create_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title content created successfully"},
    },
)
def link_title_contents(
    parent_title_id: int,
    contents: TitleContentInsert,
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    return service.insert_positioned(parent_title_id, contents, anchor="end")


@router.post(
    "/{parent_title_id}/contents/batch",
    response_model=TitleContentBatchResult,
    status_code=201,
    operation_id="attach_title_contents_batch",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "All entries appended"},
        409: {
            "description": (
                "Conflict — one or more items would close a containment cycle "
                "(`containment_cycle`) or give a title a second intrinsic parent "
                "(`intrinsic_parent_conflict`). Nothing is written. `detail` lists "
                "**every** offending item, each with its index in `loc`"
            )
        },
    },
)
def attach_title_contents_batch(
    parent_title_id: int,
    payload: TitleContentBatchInsert,
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentBatchResult:
    """Append many entries to this title's contents, as one transaction.

    The bulk form of `POST /{parent_title_id}/contents`, and the operation the placement
    queue needs: 11,945 assets are waiting to be placed, spread over directories whose
    median size is 14 and whose largest is 796.

    **All-or-nothing.** Every item is validated first; if any fails, nothing is written
    and the error names *all* of them, not the first. Following #52, which fixed the
    opposite choice one table over — by-name tagging committed once per tag, so a
    failure part-way left an arbitrary prefix written and no way to tell which.

    Failures come back in FastAPI's own validation-error shape, with the item's index in
    `loc`, so a form can highlight the offending row:

    ```json
    {"detail": [
      {"loc": ["items", 3], "msg": "Asset 91 does not exist.", "type": "target_missing"},
      {"loc": ["items", 7], "msg": "Title 4 already contains title 2, ...",
       "type": "containment_cycle"}
    ]}
    ```

    Entries land in the order given, appended after whatever the title already holds.
    There is no per-item anchor: placing forty items individually is what this exists to
    avoid, and a caller wanting a particular arrangement sends them in that order.
    """
    return service.attach_many(parent_title_id, payload.items)


@router.post(
    "/{parent_title_id}/contents/batch/detach",
    response_model=TitleContentBatchResult,
    operation_id="detach_title_contents_batch",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "All entries removed"},
    },
)
def detach_title_contents_batch(
    parent_title_id: int,
    payload: TitleContentBatchIds,
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentBatchResult:
    """Remove many entries from this title's contents, as one transaction.

    Every id must already be an entry of this title; one that is not fails the whole
    batch with a 422 naming it, on the same reasoning as the single `DELETE` (#185).
    Repeats are collapsed — asking twice for a row to be gone is not a conflict.

    `POST` rather than `DELETE` because the ids travel in a body, and a `DELETE` with a
    body is inconsistently supported by proxies and clients. The response reports how
    many rows went; there are no items to return.
    """
    return service.detach_many(parent_title_id, payload.title_contents_ids)


@router.post(
    "/{destination_title_id}/contents/batch/move",
    response_model=TitleContentBatchResult,
    operation_id="move_title_contents_batch",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "All entries moved"},
        409: {
            "description": (
                "Conflict — one or more items would close a containment cycle "
                "(`containment_cycle`) or give a title a second intrinsic parent "
                "(`intrinsic_parent_conflict`). Nothing is moved. `detail` lists every "
                "offending item, each with its index in `loc`"
            )
        },
    },
)
def move_title_contents_batch(
    destination_title_id: int,
    payload: TitleContentBatchIds,
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentBatchResult:
    """Move many entries under this title, as one transaction.

    The multi-select drag. The path title is the **destination**, matching the single
    `POST .../{id}/move` and unlike every other route here, where it is the entry's
    current parent (#185).

    Entries may come from any number of source parents; all of them land here, appended
    in the order given, and every list they leave is renumbered. Positions are never
    carried across.

    **One destination is deliberate, not a limitation.** A batch that sent each item
    somewhere different would need whole-batch cycle detection — moving A under B and B
    under A is a cycle that neither item creates alone. With a single destination every
    new edge leaves the same parent, so checking each item against the stored graph is
    complete.
    """
    return service.move_many(destination_title_id, payload.title_contents_ids)


@router.patch(
    "/{parent_title_id}/contents/{title_contents_id:int}",
    response_model=TitleContentRead,
    operation_id="update_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title content updated successfully"},
    },
)
def partial_title_content_update(
    parent_title_id: int,
    title_contents_id: int,
    update: TitleContentPatchPublic,  # type: ignore
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    return service.update_title_content(
        parent_title_id=parent_title_id,
        title_contents_id=title_contents_id,
        update=update,
        exclude_none=True,
    )


@router.delete(
    "/{parent_title_id}/contents/{title_contents_id:int}",
    status_code=204,
    operation_id="delete_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Title content deleted successfully"},
    },
)
def unlink_title_contents(
    parent_title_id: int,
    title_contents_id: int,
    service: TitleContentService = Depends(get_title_content_service),
) -> None:
    return service.unlink_content(parent_title_id, title_contents_id)


@router.post(
    "/{parent_title_id}/contents/positioned",
    response_model=TitleContentRead,
    status_code=201,
    operation_id="create_title_content_positioned",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        201: {"description": "Title content created with position successfully"},
    },
)
def create_title_content_positioned(
    parent_title_id: int,
    payload: TitleContentInsert,
    before_id: int | None = Query(None, description="Place before this id"),
    after_id: int | None = Query(None, description="Place after this id"),
    position: str | None = Query(None, description="Special position: 'start' or 'end'"),
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    # The query parameter keeps its name; below this line the anchor is called `anchor`,
    # so it cannot be confused with the integer `position` a row now carries.
    return service.insert_positioned(
        parent_title_id,
        payload,
        before_id=before_id,
        after_id=after_id,
        anchor=position,
    )


@router.post(
    "/{destination_title_id}/contents/{title_contents_id:int}/move",
    response_model=TitleContentRead,
    operation_id="move_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title content moved successfully"},
        409: {
            "description": (
                "Conflict — the move would close a containment cycle "
                "(`containment_cycle`), the child already has an intrinsic parent "
                "(`intrinsic_parent_conflict`), or the destination position is taken "
                "(`position_conflict`). The discriminator is in `detail[0].type`"
            )
        },
    },
)
def move_title_content(
    destination_title_id: int,
    title_contents_id: int,
    before_id: int | None = Query(None, description="Place before this id"),
    after_id: int | None = Query(None, description="Place after this id"),
    position: str | None = Query(None, description="Special position: 'start' or 'end'"),
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    """Move a containment edge under a different parent, atomically.

    The path title is the **destination**, not the edge's current parent -- the one
    route in this file where that is so, which is why it is a route of its own rather
    than a flag on `reorder`. Absent an anchor the edge appends to the destination's
    list; its old position is never carried across.

    `POST` rather than `PATCH` because this is an operation on a resource rather than an
    edit to its representation: the request body is empty and the interesting arguments
    are where to land, which is the same shape `/contents/positioned` already uses.
    """
    return service.move_content(
        destination_title_id,
        title_contents_id,
        before_id=before_id,
        after_id=after_id,
        anchor=position,
    )


@router.patch(
    "/{parent_title_id}/contents/{title_contents_id:int}/reorder",
    response_model=TitleContentRead,
    operation_id="reorder_title_content",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Title content reordered successfully"},
    },
)
def reorder_title_content(
    parent_title_id: int,
    title_contents_id: int,
    before_id: int | None = Query(None, description="Place before this id"),
    after_id: int | None = Query(None, description="Place after this id"),
    position: str | None = Query(None, description="Special position: 'start' or 'end'"),
    service: TitleContentService = Depends(get_title_content_service),
) -> TitleContentRead:
    """Move an entry to a different place in its parent's list.

    **One drag is one call, over any distance.** This is not a single-step nudge: the
    entry is taken out of the list, an index is chosen against what remains, and the
    list is renumbered around it, so `position=start` on the last item of a 20-item
    collection lands it at index 0 in one request. Positions stay contiguous and
    zero-based throughout; a caller never computes one.

    Say where to land with exactly one of:

    - `before_id` / `after_id` — immediately before or after that sibling
    - `position=start` / `position=end` — either end of the list

    Naming a row that is not in this parent's list, or the moved row as its own
    neighbour, leaves the entry where it is rather than flinging it to the end.

    Same-parent only. Moving an entry to a *different* parent is
    `POST /{destination_title_id}/contents/{id}/move`, which is one call as well and
    additionally rejects cycles — this route 404s if the entry is not under
    `parent_title_id`.

    **Reordering a whole list** has no single endpoint and does not need one (#180).
    Walk the target order and send each entry to the end in turn: that is N calls, it
    needs no knowledge of intermediate positions, and it converges. Measured against
    production shapes, the sets this applies to are small -- curated collections are
    what the design makes reorderable, and the largest holds 20 entries with a median
    of 1. The trade is that each call commits, so a concurrent reader can observe the
    intermediate orderings; that is the reason a set-level endpoint would exist, and
    the reason it does not yet.
    """
    return service.reorder_content(
        parent_title_id,
        title_content_id=title_contents_id,
        before_id=before_id,
        after_id=after_id,
        anchor=position,
    )
