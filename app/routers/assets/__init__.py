# app/routers/assets/__init__.py
from fastapi import APIRouter

from .core import router as core_router
from .tags import router as tags_router
from .streams import router as streams_router
from .transform_requests import router as transform_requests_router
from .metadata import router as metadata_router
from .external_ids import router as external_ids_router
from .relationships import router as relationships_router
from .files import router as files_router

router = APIRouter(tags=["assets"], redirect_slashes=False)

# Include all sub-routers - these are already prefixed with paths in their endpoints
router.include_router(core_router)
router.include_router(tags_router)
router.include_router(streams_router)
router.include_router(transform_requests_router)
router.include_router(metadata_router)
router.include_router(external_ids_router)
router.include_router(relationships_router)
router.include_router(files_router)
