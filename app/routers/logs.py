# app/routers/logs.py
from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response, Depends

from app.config import LogfireConfig, get_logfire_config

router = APIRouter()

# Default content type for OTLP traces
DEFAULT_CONTENT_TYPE = "application/x-protobuf"
# Logging request timeout in seconds
DEFAULT_TIMEOUT = 5


@router.post("", operation_id="ingest_client_traces")
async def client_traces(
    request: Request,
    config: LogfireConfig = Depends(get_logfire_config),
) -> Response:

    body = await request.body()
    content_type = request.headers.get("content-type", DEFAULT_CONTENT_TYPE)

    headers: dict[str, str] = {
        "Content-Type": content_type,
        "Authorization": config.token,
    }

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        r = await client.post(
            f"{config.base_url}/v1/traces",
            content=body,
            headers=headers,
        )
    return Response(status_code=r.status_code, content=r.content)
