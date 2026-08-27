# app/routers/titles/artwork.py
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from app.api_responses import COMMON_READ_RESPONSES
from app.dependencies import get_artwork_service
from app.routers.artwork import ARTWORK_UPLOAD_RESPONSES, artwork_upload_form
from app.routers.base import QuietClientErrorRoute
from app.schemas import ArtworkRead, ArtworkUploadForm
from app.schemas.enums import EntityTypeEnum
from app.services import ArtworkService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{title_id}/artwork",
    response_model=list[ArtworkRead],
    operation_id="list_title_artwork",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of artwork retrieved successfully"},
    },
)
def list_title_artwork(
    title_id: int,
    kind: str | None = None,
    service: ArtworkService = Depends(get_artwork_service),
) -> list[ArtworkRead]:
    """This title's own artwork, primary first.

    Only artwork registered against this title. A title with none of its own resolves
    one from its contents, which is a separate concern (#105) and not this endpoint.
    """
    return service.list_artwork(EntityTypeEnum.title, title_id, kind)


@router.post(
    "/{title_id}/artwork",
    response_model=ArtworkRead,
    status_code=201,
    operation_id="upload_title_artwork",
    responses=ARTWORK_UPLOAD_RESPONSES,
)
def upload_title_artwork(
    title_id: int,
    file: Annotated[UploadFile, File(description="The image file to store")],
    upload: ArtworkUploadForm = Depends(artwork_upload_form),
    service: ArtworkService = Depends(get_artwork_service),
) -> ArtworkRead:
    """Store an uploaded image and register it as this title's artwork.

    The file's type is determined from its bytes rather than from the submitted
    filename or content type, and its storage path from a digest of its contents, so
    neither is a field the caller supplies.
    """
    return service.register_upload(EntityTypeEnum.title, title_id, upload, file.file)
