# app/models/__init__.py

from app.database import Base

# Import event listeners to register them with SQLAlchemy
from . import events  # noqa: F401
from .artwork import DEFAULT_ARTWORK_KINDS, ArtworkKindORM, ArtworkORM
from .asset import AssetORM
from .job import JobORM
from .metadata import MetadataORM
from .run_summary import RunSummaryORM, ScannerRunSummaryORM
from .runner_state import RunnerStateORM
from .stream import StreamORM
from .tag import AssetTagORM, TagORM, TitleTagORM
from .title import TitleORM
from .title_contents import TitleContentORM
from .title_reference import TitleReferenceORM
from .title_type import DEFAULT_TITLE_TYPES, TitleTypeORM
from .transform_request import TransformRequestORM
from .id_scheme import IdSchemeORM, AssetIdORM, ExternalIdentifierORM

__all__ = [
    "ArtworkKindORM",
    "ArtworkORM",
    "DEFAULT_ARTWORK_KINDS",
    "AssetORM",
    "AssetTagORM",
    "Base",
    "JobORM",
    "MetadataORM",
    "RunSummaryORM",
    "RunnerStateORM",
    "ScannerRunSummaryORM",
    "StreamORM",
    "TagORM",
    "TitleContentORM",
    "TitleORM",
    "TitleReferenceORM",
    "TitleTagORM",
    "TitleTypeORM",
    "DEFAULT_TITLE_TYPES",
    "TransformRequestORM",
    "AssetIdORM",
    "ExternalIdentifierORM",
    "IdSchemeORM",
]
