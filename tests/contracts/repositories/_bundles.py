# tests/contracts/repositories/_bundles.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only imported for type checking; no runtime import cycles
    from app.repositories.protocols import (
        ArtworkKindRepository,
        ArtworkRepository,
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
    )


@dataclass
class BaseBundle:
    close: Callable[[], None]


@dataclass
class MediaRepoBundle(BaseBundle):
    assets: MediaRepository
    tags: TagRepository


@dataclass
class StreamRepoBundle(BaseBundle):
    assets: MediaRepository
    streams: StreamRepository


@dataclass
class RunSummaryRepoBundle(BaseBundle):
    run_summary: RunSummaryRepository


@dataclass
class ScannerRunSummaryRepoBundle(BaseBundle):
    scanner_run_summary: ScannerRunSummaryRepository


@dataclass
class TitleRepoBundle(BaseBundle):
    titles: TitleRepository
    title_references: TitleReferenceRepository
    tags: TagRepository


@dataclass
class TitleReferenceRepoBundle(BaseBundle):
    titles: TitleRepository
    title_references: TitleReferenceRepository


@dataclass
class TitleContentRepoBundle(BaseBundle):
    titles: TitleRepository
    assets: MediaRepository
    title_contents: TitleContentRepository


@dataclass
class TransformRequestRepoBundle(BaseBundle):
    assets: MediaRepository
    transform_requests: TransformRequestRepository


@dataclass
class TagRepoBundle(BaseBundle):
    tags: TagRepository
    assets: MediaRepository
    titles: TitleRepository


@dataclass
class MetadataRepoBundle(BaseBundle):
    assets: MediaRepository
    metadata: MetadataRepository


@dataclass
class IdSchemeRepoBundle(BaseBundle):
    id_schemes: IdSchemeRepository
    assets: MediaRepository


@dataclass
class InboxRepoBundle(BaseBundle):
    inbox: InboxRepository


@dataclass
class RunnerStateRepoBundle(BaseBundle):
    runner_state: RunnerStateRepository


@dataclass
class JobRepoBundle(BaseBundle):
    jobs: JobRepository


@dataclass
class ExternalIdentifierRepoBundle(BaseBundle):
    external_identifiers: ExternalIdentifierRepository
    id_schemes: IdSchemeRepository
    assets: MediaRepository
    titles: TitleRepository


@dataclass
class ArtworkRepoBundle(BaseBundle):
    artwork: ArtworkRepository
    artwork_kinds: ArtworkKindRepository
    id_schemes: IdSchemeRepository
    assets: MediaRepository
    titles: TitleRepository
