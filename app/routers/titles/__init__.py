# app/routers/titles/__init__.py
from fastapi import APIRouter

from .artwork import router as artwork_router
from .core import router as core_router
from .references import router as references_router
from .contents import router as contents_router
from .tags import router as tags_router
from .external_ids import router as external_ids_router

router = APIRouter(redirect_slashes=False)

# Include all sub-routers
router.include_router(core_router)
router.include_router(artwork_router)
router.include_router(references_router)
router.include_router(contents_router)
router.include_router(tags_router)
router.include_router(external_ids_router)
