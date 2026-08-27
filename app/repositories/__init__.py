# app/repositories/__init__.py
"""Repository layer for data access.

Re-exports are resolved lazily. Importing this package used to pull in every
repository module eagerly, and those modules reach back into ``app.models`` and
``app.utils.sorting`` — so merely importing a leaf such as ``app.repositories.errors``
dragged the whole layer in and produced a circular import. Callers worked around
it with function-local imports.

Deferring the work to ``__getattr__`` keeps ``from app.repositories import X``
working exactly as before while leaving this module's own import cheap, which is
what breaks the cycle. See issue #32.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Import eagerly for type checkers and IDEs only; never at runtime.
    from .artwork_repository import (
        SQLAlchemyArtworkKindRepository,
        SQLAlchemyArtworkRepository,
    )
    from .external_identifier_repository import SQLAlchemyExternalIdentifierRepository
    from .id_scheme_repository import SQLAlchemyIdSchemeRepository
    from .inbox_repository import FileInboxRepository
    from .job_repository import SQLAlchemyJobRepository
    from .media_repository import SQLAlchemyMediaRepository
    from .metadata_repository import SQLAlchemyMetadataRepository
    from .protocols import (
        ArtworkKindRepository,
        ArtworkRepository,
        ExternalIdentifierRepository,
        IdSchemeRepository,
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
        TitleTypeRepository,
        TransformRequestRepository,
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
    from .title_type_repository import SQLAlchemyTitleTypeRepository
    from .transform_request_repository import SQLAlchemyTransformRequestRepository

# Exported name -> the submodule that defines it.
_EXPORTS: dict[str, str] = {
    "SQLAlchemyArtworkKindRepository": ".artwork_repository",
    "SQLAlchemyArtworkRepository": ".artwork_repository",
    "SQLAlchemyExternalIdentifierRepository": ".external_identifier_repository",
    "SQLAlchemyIdSchemeRepository": ".id_scheme_repository",
    "FileInboxRepository": ".inbox_repository",
    "SQLAlchemyJobRepository": ".job_repository",
    "SQLAlchemyMediaRepository": ".media_repository",
    "SQLAlchemyMetadataRepository": ".metadata_repository",
    "ArtworkKindRepository": ".protocols",
    "ArtworkRepository": ".protocols",
    "ExternalIdentifierRepository": ".protocols",
    "IdSchemeRepository": ".protocols",
    "InboxRepository": ".protocols",
    "JobRepository": ".protocols",
    "MediaRepository": ".protocols",
    "MetadataRepository": ".protocols",
    "RunSummaryRepository": ".protocols",
    "RunnerStateRepository": ".protocols",
    "ScannerRunSummaryRepository": ".protocols",
    "StreamRepository": ".protocols",
    "TagRepository": ".protocols",
    "TitleContentRepository": ".protocols",
    "TitleReferenceRepository": ".protocols",
    "TitleRepository": ".protocols",
    "TitleTypeRepository": ".protocols",
    "TransformRequestRepository": ".protocols",
    "SQLAlchemyRunSummaryRepository": ".run_summary_repository",
    "SQLAlchemyScannerRunSummaryRepository": ".run_summary_repository",
    "SQLAlchemyRunnerStateRepository": ".runner_state_repository",
    "SQLAlchemyStreamRepository": ".stream_repository",
    "SQLAlchemyTagRepository": ".tag_repository",
    "SQLAlchemyTitleContentRepository": ".title_content_repository",
    "SQLAlchemyTitleReferenceRepository": ".title_reference_repository",
    "SQLAlchemyTitleRepository": ".title_repository",
    "SQLAlchemyTitleTypeRepository": ".title_type_repository",
    "SQLAlchemyTransformRequestRepository": ".transform_request_repository",
}


if not TYPE_CHECKING:
    # Hidden from type checkers on purpose. A visible module-level __getattr__ makes
    # mypy treat *every* name as importable from this package, which would silently
    # stop it catching `from app.repositories import Misspelled`. Type checkers use
    # the TYPE_CHECKING imports above instead, so unknown names stay errors.

    def __getattr__(name: str) -> Any:
        """Resolve a re-exported name on first access (PEP 562)."""
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(import_module(module, __name__), name)
        globals()[name] = value  # cache, so later lookups skip __getattr__
        return value

    def __dir__() -> list[str]:
        return sorted(__all__)


__all__ = [
    "ArtworkKindRepository",
    "ArtworkRepository",
    "ExternalIdentifierRepository",
    "FileInboxRepository",
    "IdSchemeRepository",
    "InboxRepository",
    "JobRepository",
    "MediaRepository",
    "MetadataRepository",
    "RunSummaryRepository",
    "RunnerStateRepository",
    "SQLAlchemyArtworkKindRepository",
    "SQLAlchemyArtworkRepository",
    "SQLAlchemyExternalIdentifierRepository",
    "SQLAlchemyIdSchemeRepository",
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
    "SQLAlchemyTitleTypeRepository",
    "SQLAlchemyTransformRequestRepository",
    "ScannerRunSummaryRepository",
    "StreamRepository",
    "TagRepository",
    "TitleContentRepository",
    "TitleReferenceRepository",
    "TitleRepository",
    "TitleTypeRepository",
    "TransformRequestRepository",
]
