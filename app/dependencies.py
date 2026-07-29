# app/dependencies.py
from __future__ import annotations

from collections.abc import Generator

from elasticsearch import Elasticsearch
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import (
    ElasticsearchConfig,
    MediaConfig,
    get_es_config,
    get_media_config,
)
from app.database import get_session_factory
from app.elasticsearch_client import get_es_manager
from app.orchestration.registry import ProviderRegistry, get_provider_registry
from app.repositories import (
    FileInboxRepository,
    InboxRepository,
    JobRepository,
    MediaRepository,
    RunnerStateRepository,
    RunSummaryRepository,
    ScannerRunSummaryRepository,
    SQLAlchemyExternalIdentifierRepository,
    SQLAlchemyIdSchemeRepository,
    SQLAlchemyJobRepository,
    SQLAlchemyMediaRepository,
    SQLAlchemyMetadataRepository,
    SQLAlchemyRunnerStateRepository,
    SQLAlchemyRunSummaryRepository,
    SQLAlchemyScannerRunSummaryRepository,
    SQLAlchemyStreamRepository,
    SQLAlchemyTagRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleReferenceRepository,
    SQLAlchemyTitleRepository,
    SQLAlchemyTransformRequestRepository,
    TitleRepository,
)
from app.services import (
    ExternalIdentifierService,
    FileStreamService,
    IdSchemeService,
    InboxService,
    JobService,
    MediaService,
    MetadataService,
    RunnerStateService,
    RunSummaryService,
    ScannerRunSummaryService,
    StreamService,
    TagService,
    TitleContentService,
    TitleReferenceService,
    TitleService,
    TranscriptSearchService,
    TransformRequestService,
)

# --------------------------- Session


def get_db_session() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


# --------------------------- External clients


def get_es_client() -> Elasticsearch:
    try:
        es_manager = get_es_manager()
        return es_manager.get_client()
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail="Search service is temporarily unavailable",
        ) from e


# --------------------------- Repositories
# Single-repo services may use these named repository dependencies directly.
# Multi-repo service factories construct repos from the session inline.


def get_media_repository(
    db: Session = Depends(get_db_session),
) -> MediaRepository:
    return SQLAlchemyMediaRepository(db)


def get_run_summary_repository(
    db: Session = Depends(get_db_session),
) -> RunSummaryRepository:
    return SQLAlchemyRunSummaryRepository(db)


def get_scanner_run_summary_repository(
    db: Session = Depends(get_db_session),
) -> ScannerRunSummaryRepository:
    return SQLAlchemyScannerRunSummaryRepository(db)


def get_runner_state_repository(
    db: Session = Depends(get_db_session),
) -> RunnerStateRepository:
    return SQLAlchemyRunnerStateRepository(db)


def get_title_repository(
    db: Session = Depends(get_db_session),
) -> TitleRepository:
    return SQLAlchemyTitleRepository(db)


def get_job_repository(
    db: Session = Depends(get_db_session),
) -> JobRepository:
    return SQLAlchemyJobRepository(db)


def get_inbox_repository(config: MediaConfig = Depends(get_media_config)) -> InboxRepository:
    return FileInboxRepository(config)


# --------------------------- Services


def get_media_service(
    media_repo: MediaRepository = Depends(get_media_repository),
    config: MediaConfig = Depends(get_media_config),
) -> MediaService:
    return MediaService(media_repo, config)


def get_file_stream_service(
    media_service: MediaService = Depends(get_media_service),
    config: MediaConfig = Depends(get_media_config),
) -> FileStreamService:
    return FileStreamService(media_service, config)


def get_stream_service(db: Session = Depends(get_db_session)) -> StreamService:
    return StreamService(SQLAlchemyStreamRepository(db), SQLAlchemyMediaRepository(db))


def get_transform_request_service(
    db: Session = Depends(get_db_session),
    provider_registry: ProviderRegistry = Depends(get_provider_registry),
) -> TransformRequestService:
    return TransformRequestService(
        SQLAlchemyTransformRequestRepository(db),
        SQLAlchemyMediaRepository(db),
        provider_registry,
    )


def get_run_summary_service(
    run_summary_repo: RunSummaryRepository = Depends(get_run_summary_repository),
) -> RunSummaryService:
    return RunSummaryService(run_summary_repo)


def get_scanner_run_summary_service(
    scanner_run_summary_repo: ScannerRunSummaryRepository = Depends(
        get_scanner_run_summary_repository
    ),
) -> ScannerRunSummaryService:
    return ScannerRunSummaryService(scanner_run_summary_repo)


def get_runner_state_service(
    runner_state_repo: RunnerStateRepository = Depends(get_runner_state_repository),
) -> RunnerStateService:
    return RunnerStateService(runner_state_repo)


def get_title_service(
    title_repo: TitleRepository = Depends(get_title_repository),
) -> TitleService:
    return TitleService(title_repo)


def get_title_reference_service(db: Session = Depends(get_db_session)) -> TitleReferenceService:
    return TitleReferenceService(
        SQLAlchemyTitleRepository(db),
        SQLAlchemyTitleReferenceRepository(db),
    )


def get_title_content_service(db: Session = Depends(get_db_session)) -> TitleContentService:
    return TitleContentService(
        SQLAlchemyTitleRepository(db),
        SQLAlchemyTitleContentRepository(db),
        SQLAlchemyMediaRepository(db),
    )


def get_tag_service(db: Session = Depends(get_db_session)) -> TagService:
    return TagService(
        SQLAlchemyTagRepository(db),
        SQLAlchemyMediaRepository(db),
        SQLAlchemyTitleRepository(db),
    )


def get_inbox_service(
    db: Session = Depends(get_db_session),
    config: MediaConfig = Depends(get_media_config),
) -> InboxService:
    return InboxService(
        FileInboxRepository(config),
        SQLAlchemyMediaRepository(db),
        SQLAlchemyTransformRequestRepository(db),
    )


def get_transcript_search_service(
    es: Elasticsearch = Depends(get_es_client),
    config: ElasticsearchConfig = Depends(get_es_config),
) -> TranscriptSearchService:
    return TranscriptSearchService(es, config)


def get_metadata_service(db: Session = Depends(get_db_session)) -> MetadataService:
    return MetadataService(
        SQLAlchemyMetadataRepository(db),
        SQLAlchemyMediaRepository(db),
    )


def get_job_service(
    job_repo: JobRepository = Depends(get_job_repository),
) -> JobService:
    return JobService(job_repo)


def get_id_scheme_service(db: Session = Depends(get_db_session)) -> IdSchemeService:
    return IdSchemeService(
        SQLAlchemyIdSchemeRepository(db),
        SQLAlchemyExternalIdentifierRepository(db),
    )


def get_external_identifier_service(
    db: Session = Depends(get_db_session),
) -> ExternalIdentifierService:
    return ExternalIdentifierService(
        SQLAlchemyExternalIdentifierRepository(db),
        SQLAlchemyMediaRepository(db),
        SQLAlchemyTitleRepository(db),
    )
