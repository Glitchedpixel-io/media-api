# app/routers/search_transcripts.py
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_transcript_search_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import (
    TranscriptSearchHit,
    TranscriptSearchQuery,
    TranscriptSearchResponse,
)
from app.services import TranscriptSearchService

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/transcripts",
    response_model=TranscriptSearchResponse,
    operation_id="search_transcripts",
)
def search_transcripts(
    query: TranscriptSearchQuery = Depends(),
    svc: TranscriptSearchService = Depends(get_transcript_search_service),
) -> TranscriptSearchResponse:
    # Values are normalized at schema level via validators
    data = svc.search(
        q=query.q,
        mode=query.mode,
        size=query.size,
        offset=query.offset,
        path_prefix=query.path_prefix,
        path_part=query.path_part,
        collection=query.collection,
        title_part=query.title_part,
        asset_id=query.asset_id,
        language=query.language,
    )

    # Bubble up a meaningful error to clients if ES is unavailable
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        # Use 503 Service Unavailable for ES connection issues
        status = 503 if err.get("code") == "es_unavailable" else 500
        raise HTTPException(status_code=status, detail=err.get("message") or "Search error")

    # Coerce to typed response
    items = [TranscriptSearchHit(**item) for item in data.get("items", [])]
    total = int(data.get("total", 0))
    return TranscriptSearchResponse(items=items, total=total)
