# app/routers/artwork.py
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import ValidationError

from app.api_responses import COMMON_READ_RESPONSES, COMMON_WRITE_RESPONSES
from app.dependencies import get_artwork_kind_service, get_artwork_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    ArtworkKindRead,
    ArtworkListParams,
    ArtworkPatchPublic,
    ArtworkRead,
    ArtworkUploadForm,
    PaginatedResponse,
)
from app.services import ArtworkKindService, ArtworkService

router = APIRouter(route_class=QuietClientErrorRoute)

#: The refusals an artwork upload can produce, each from a distinct cause.
#:
#: Kept distinct on purpose. `QuietClientErrorRoute` only silences a client error if
#: the status reaching the route is already right (see CLAUDE.md), so collapsing "not
#: an image", "too large" and "unknown kind" into one 500-shaped exception would page
#: on every caller mistake and the route class could do nothing about it.
ARTWORK_UPLOAD_RESPONSES: dict[int | str, dict] = {
    **COMMON_READ_RESPONSES,
    **COMMON_WRITE_RESPONSES,
    201: {"description": "Artwork stored and registered successfully"},
    400: {"description": "Bad Request - the uploaded file is empty"},
    413: {"description": "Payload Too Large - the uploaded file exceeds the size cap"},
    415: {"description": "Unsupported Media Type - the file is not a supported image"},
}


def artwork_upload_form(
    artwork_kind: Annotated[str, Form(description="Code of the artwork kind, e.g. poster")],
    is_primary: Annotated[bool, Form(description="Make this the artwork for its kind")] = False,
    width: Annotated[int | None, Form(description="Pixel width, when known")] = None,
    height: Annotated[int | None, Form(description="Pixel height, when known")] = None,
    source_scheme_id: Annotated[int | None, Form(description="Source ID scheme")] = None,
    source_external_id: Annotated[str | None, Form(description="ID within the scheme")] = None,
    source_url: Annotated[str | None, Form(description="Where it was fetched from")] = None,
) -> ArtworkUploadForm:
    """Collect the multipart fields accompanying an artwork upload.

    The fields are spelled out rather than declared as ``Annotated[ArtworkUploadForm,
    Form()]`` because FastAPI does not flatten a form model that shares a request with
    a ``File`` parameter -- it looks for a single field literally named ``upload`` and
    422s when it is absent. Listing them here keeps the generated multipart schema
    accurate for clients built from the OpenAPI document.

    Validation still belongs to the model, not to this signature, so the cross-field
    provenance rule holds for uploads exactly as it does for every other write.

    Raises:
        HTTPException: 422 if the submitted fields fail the model's validation.
    """
    try:
        return ArtworkUploadForm(
            artwork_kind=artwork_kind,
            is_primary=is_primary,
            width=width,
            height=height,
            source_scheme_id=source_scheme_id,
            source_external_id=source_external_id,
            source_url=source_url,
        )
    except ValidationError as e:
        # Without this the ValidationError escapes as a 500 and Logfire pages on a
        # caller mistake -- the failure mode CLAUDE.md warns QuietClientErrorRoute
        # cannot undo, because by then the status is already wrong.
        #
        # All three flags are load-bearing. `ctx` carries the original ValueError
        # object for a validator that raised one, which is not JSON serialisable, so
        # leaving include_context on turns this 422 into a 500 during response
        # encoding -- the exact outcome it exists to prevent. `input` would echo the
        # caller's submitted values back, and `url` points at pydantic's docs.
        raise HTTPException(
            status_code=422,
            detail=e.errors(include_url=False, include_context=False, include_input=False),
        ) from e


@router.get(
    "",
    response_model=PaginatedResponse[ArtworkRead],
    operation_id="list_artwork",
)
def list_artwork(
    params: ArtworkListParams = Depends(),
    service: ArtworkService = Depends(get_artwork_service),
) -> PaginatedResponse[ArtworkRead]:
    """Every artwork, filtered and paged.

    Declared before ``/{artwork_id}`` so the empty path is matched as a collection
    rather than as an id, and capped by ``KeysetPagination`` like the other listings --
    the nested per-entity routes are uncapped, which is not a shape to copy.
    """
    return service.list_all_artwork(params)


@router.get(
    "/{artwork_id}",
    response_model=ArtworkRead,
    operation_id="get_artwork",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "Artwork retrieved successfully"},
    },
)
def get_artwork(
    artwork_id: int, service: ArtworkService = Depends(get_artwork_service)
) -> ArtworkRead:
    return service.get_artwork(artwork_id)


@router.patch(
    "/{artwork_id}",
    response_model=ArtworkRead,
    operation_id="update_artwork",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        200: {"description": "Artwork updated successfully"},
    },
)
def update_artwork(
    artwork_id: int,
    update: ArtworkPatchPublic,
    service: ArtworkService = Depends(get_artwork_service),
) -> ArtworkRead:
    """Update the artwork metadata a client is allowed to assert.

    Setting ``is_primary`` to true here demotes whichever artwork currently holds that
    position for the same entity and kind; the service does that rather than writing
    the flag straight through, which would collide with the unique index.

    ``storage_path``, ``mime``, ``width`` and ``height`` are **not** accepted: the
    server established them from the uploaded bytes, and submitting one is a 422 rather
    than a silent no-op. See ``ArtworkPatchPublic``.
    """
    return service.update_artwork(artwork_id, update, exclude_none=True)


@router.delete(
    "/{artwork_id}",
    status_code=204,
    operation_id="delete_artwork",
    responses={
        **COMMON_READ_RESPONSES,
        **COMMON_WRITE_RESPONSES,
        204: {"description": "Artwork deleted successfully"},
    },
)
def delete_artwork(artwork_id: int, service: ArtworkService = Depends(get_artwork_service)) -> None:
    """Delete an artwork record.

    The stored file is deliberately left in place: it is content addressed, so other
    artwork rows may reference the same bytes.
    """
    service.delete_artwork(artwork_id)


kinds_router = APIRouter(route_class=QuietClientErrorRoute)


@kinds_router.get(
    "",
    response_model=list[ArtworkKindRead],
    operation_id="list_artwork_kinds",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of artwork kinds retrieved successfully"},
    },
)
def list_artwork_kinds(
    service: ArtworkKindService = Depends(get_artwork_kind_service),
) -> list[ArtworkKindRead]:
    """The artwork kinds an upload may declare.

    Read-only for now. A client has to be able to discover the valid codes before it
    can register anything, but nothing yet needs to *create* a kind at runtime -- the
    six seeded by the migration cover every case asked for, and the lookup table
    exists so that adding a seventh is a row edit rather than a migration when
    something does.
    """
    return service.get_artwork_kinds()
