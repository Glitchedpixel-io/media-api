# app/routers/assets/files.py
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api_responses import COMMON_READ_RESPONSES
from app.config import MediaConfig, get_media_config
from app.dependencies import get_media_service
from app.routers.base import QuietClientErrorRoute
from app.schemas import AccessoryFilePage
from app.services import MediaService
from app.utils.paths import accessory_relative_path, resolve_under_root

router = APIRouter(route_class=QuietClientErrorRoute)


@router.get(
    "/{asset_id}/accessories",
    response_model=AccessoryFilePage,
    operation_id="list_asset_accessories",
    responses={
        **COMMON_READ_RESPONSES,
        200: {"description": "List of accessory files retrieved successfully"},
        400: {"description": "Invalid accessory path"},
    },
)
def list_accessories(
    asset_id: int,
    service: MediaService = Depends(get_media_service),
    config: MediaConfig = Depends(get_media_config),
) -> AccessoryFilePage:
    service.get_asset(asset_id)

    relative = accessory_relative_path(asset_id)

    accessory_root = Path(config.accessory_root).resolve()

    # Security: build and ensure path stays under root
    try:
        acc_dir = resolve_under_root(relative, accessory_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid accessory path")

    items: list = []
    if acc_dir.exists() and acc_dir.is_dir():
        try:
            for entry in os.scandir(acc_dir):
                if entry.is_file():
                    st = entry.stat()
                    items.append(
                        {
                            "filename": entry.name,
                            "size": int(st.st_size),
                            "mtime": float(st.st_mtime),
                        }
                    )
        except PermissionError:
            # Treat as empty if we cannot read contents
            items = []
    else:
        # Directory doesn't exist yet; treat as empty list
        items = []

    return AccessoryFilePage.model_validate({"items": items, "asset_id": asset_id})
