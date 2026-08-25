# tests/contracts/repositories/bundles_impl.py
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import AppConfig
from app.models import Base
from app.repositories import (
    FileInboxRepository,
    SQLAlchemyJobRepository,
    SQLAlchemyMediaRepository,
    SQLAlchemyMetadataRepository,
    SQLAlchemyRunSummaryRepository,
    SQLAlchemyRunnerStateRepository,
    SQLAlchemyScannerRunSummaryRepository,
    SQLAlchemyStreamRepository,
    SQLAlchemyTagRepository,
    SQLAlchemyTitleContentRepository,
    SQLAlchemyTitleReferenceRepository,
    SQLAlchemyTitleRepository,
    SQLAlchemyTransformRequestRepository,
    SQLAlchemyIdSchemeRepository,
    SQLAlchemyExternalIdentifierRepository,
)

from ._bundles import (
    InboxRepoBundle,
    JobRepoBundle,
    MediaRepoBundle,
    MetadataRepoBundle,
    RunSummaryRepoBundle,
    RunnerStateRepoBundle,
    ScannerRunSummaryRepoBundle,
    StreamRepoBundle,
    TagRepoBundle,
    TitleContentRepoBundle,
    TitleReferenceRepoBundle,
    TitleRepoBundle,
    TransformRequestRepoBundle,
    IdSchemeRepoBundle,
    ExternalIdentifierRepoBundle,
)


def _sqlite_engine(db_path: str):
    eng = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


def _sqlite_session(tmp_path):
    eng = _sqlite_engine(str(tmp_path / "test.db"))
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)
    sess = Session()
    return sess, eng


def asset_bundler(session, engine) -> MediaRepoBundle:
    return MediaRepoBundle(
        assets=SQLAlchemyMediaRepository(session),
        tags=SQLAlchemyTagRepository(session),
        # Only close the session; the engine is managed by the test fixture
        close=lambda: (session.close()),
    )


def stream_bundler(session, engine) -> StreamRepoBundle:
    return StreamRepoBundle(
        assets=SQLAlchemyMediaRepository(session),
        streams=SQLAlchemyStreamRepository(session),
        close=lambda: (session.close()),
    )


def run_summary_bundler(session, engine) -> RunSummaryRepoBundle:
    return RunSummaryRepoBundle(
        run_summary=SQLAlchemyRunSummaryRepository(session),
        close=lambda: (session.close()),
    )


def scanner_run_summary_bundler(session, engine) -> ScannerRunSummaryRepoBundle:
    return ScannerRunSummaryRepoBundle(
        scanner_run_summary=SQLAlchemyScannerRunSummaryRepository(session),
        close=lambda: (session.close()),
    )


def runner_state_bundler(session, engine) -> RunnerStateRepoBundle:
    return RunnerStateRepoBundle(
        runner_state=SQLAlchemyRunnerStateRepository(session),
        close=lambda: (session.close()),
    )


def title_bundler(session, engine) -> TitleRepoBundle:
    return TitleRepoBundle(
        titles=SQLAlchemyTitleRepository(session),
        title_references=SQLAlchemyTitleReferenceRepository(session),
        tags=SQLAlchemyTagRepository(session),
        close=lambda: (session.close()),
    )


def title_reference_bundler(session, engine) -> TitleReferenceRepoBundle:
    return TitleReferenceRepoBundle(
        titles=SQLAlchemyTitleRepository(session),
        title_references=SQLAlchemyTitleReferenceRepository(session),
        close=lambda: (session.close()),
    )


def title_content_bundler(session, engine) -> TitleContentRepoBundle:
    return TitleContentRepoBundle(
        titles=SQLAlchemyTitleRepository(session),
        assets=SQLAlchemyMediaRepository(session),
        title_contents=SQLAlchemyTitleContentRepository(session),
        close=lambda: (session.close()),
    )


def transform_request_bundler(session, engine) -> TransformRequestRepoBundle:
    return TransformRequestRepoBundle(
        assets=SQLAlchemyMediaRepository(session),
        transform_requests=SQLAlchemyTransformRequestRepository(session),
        close=lambda: (session.close()),
    )


def tag_bundler(session, engine) -> TagRepoBundle:
    return TagRepoBundle(
        assets=SQLAlchemyMediaRepository(session),
        tags=SQLAlchemyTagRepository(session),
        titles=SQLAlchemyTitleRepository(session),
        close=lambda: (session.close()),
    )


def metadata_bundler(session, engine) -> MetadataRepoBundle:
    return MetadataRepoBundle(
        assets=SQLAlchemyMediaRepository(session),
        metadata=SQLAlchemyMetadataRepository(session),
        close=lambda: (session.close()),
    )


def job_bundler(session, engine) -> JobRepoBundle:
    return JobRepoBundle(
        jobs=SQLAlchemyJobRepository(session),
        close=lambda: (session.close()),
    )


def idscheme_bundler(session, engine) -> IdSchemeRepoBundle:
    return IdSchemeRepoBundle(
        id_schemes=SQLAlchemyIdSchemeRepository(session),
        assets=SQLAlchemyMediaRepository(session),
        close=lambda: (session.close()),
    )


def inbox_bundler(test_settings: AppConfig) -> InboxRepoBundle:
    return InboxRepoBundle(
        inbox=FileInboxRepository(test_settings.media),
        close=lambda: (print("finished with FileInboxRepository")),
    )


def external_identifier_bundler(session, engine) -> ExternalIdentifierRepoBundle:
    return ExternalIdentifierRepoBundle(
        external_identifiers=SQLAlchemyExternalIdentifierRepository(session),
        id_schemes=SQLAlchemyIdSchemeRepository(session),
        assets=SQLAlchemyMediaRepository(session),
        titles=SQLAlchemyTitleRepository(session),
        close=lambda: (session.close()),
    )


def make_bundle(session, engine, bundler):
    return bundler(session, engine)
