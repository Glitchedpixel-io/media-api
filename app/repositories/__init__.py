# app/repositories/__init__.py
"""Repository layer for data access."""

from .inbox_repository import FileInboxRepository
from .job_repository import SQLAlchemyJobRepository
from .media_repository import SQLAlchemyMediaRepository
from .metadata_repository import SQLAlchemyMetadataRepository
from .protocols import (
    InboxRepository,
    JobRepository,
    MediaRepository,
    MetadataRepository,
    RunSummaryRepository,
    RunnerStateRepository,
    ScannerRunSummaryRepository,
    StreamRepository,
    TagRepository,
    TitleContentRepository,
    TitleReferenceRepository,
    TitleRepository,
    TransformRequestRepository,
    IdSchemeRepository,
    ExternalIdentifierRepository,
)
from .run_summary_repository import (
    SQLAlchemyRunSummaryRepository,
    SQLAlchemyScannerRunSummaryRepository,
)
from .runner_state_repository import SQLAlchemyRunnerStateRepository
from .stream_repository import SQLAlchemyStreamRepository
from .tag_repository import SQLAlchemyTagRepository
from .title_content_repository import SQLAlchemyTitleContentRepository
from .title_reference_repository import SQLAlchemyTitleReferenceRepository
from .title_repository import SQLAlchemyTitleRepository
from .transform_request_repository import SQLAlchemyTransformRequestRepository
from .id_scheme_repository import SQLAlchemyIdSchemeRepository
from .external_identifier_repository import SQLAlchemyExternalIdentifierRepository

__all__ = [
    "FileInboxRepository",
    "InboxRepository",
    "JobRepository",
    "MediaRepository",
    "MetadataRepository",
    "RunSummaryRepository",
    "RunnerStateRepository",
    "SQLAlchemyJobRepository",
    "SQLAlchemyMediaRepository",
    "SQLAlchemyMetadataRepository",
    "SQLAlchemyRunSummaryRepository",
    "SQLAlchemyRunnerStateRepository",
    "SQLAlchemyScannerRunSummaryRepository",
    "SQLAlchemyStreamRepository",
    "SQLAlchemyTagRepository",
    "SQLAlchemyTitleContentRepository",
    "SQLAlchemyTitleReferenceRepository",
    "SQLAlchemyTitleRepository",
    "SQLAlchemyTransformRequestRepository",
    "SQLAlchemyIdSchemeRepository",
    "SQLAlchemyExternalIdentifierRepository",
    "ScannerRunSummaryRepository",
    "StreamRepository",
    "TagRepository",
    "TitleContentRepository",
    "TitleReferenceRepository",
    "TitleRepository",
    "TransformRequestRepository",
    "IdSchemeRepository",
    "ExternalIdentifierRepository",
]
