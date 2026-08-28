# app/schemas/enums.py
import enum


class OutcomeEnum(str, enum.Enum):
    """Enum for processing outcomes"""

    cancelled = "cancelled"
    succeeded = "succeeded"
    failed = "failed"


class TitleReferenceTypeEnum(str, enum.Enum):
    """Enum for title references"""

    review = "review"
    metadata = "metadata"
    article = "article"
    summary = "summary"
    other = "other"


class ContentKind(enum.Enum):
    asset = "asset"
    title = "title"


class MembershipKind(enum.Enum):
    """Why a ``title_contents`` row exists: an item's home, or a curated list.

    Intrinsic containment is structural -- Series -> Season -> Episode -- and defines
    where an item lives. It drives breadcrumbs, and #90 enforces at most one intrinsic
    parent per title so that a breadcrumb has a single well-defined path upward.

    Curated containment is lateral. "Oscar Winners 2023" contains titles that live
    elsewhere, so a curated edge says nothing about where its child belongs and
    aggregates must not count it (#96).

    Member names match their values deliberately. ``sqlalchemy.Enum`` persists the
    *name* of a Python enum member rather than its value, so a mismatch here would
    store one string and serialise another.
    """

    intrinsic = "intrinsic"
    curated = "curated"


class EntityTypeEnum(str, enum.Enum):
    """Enum for external identifier entity types"""

    asset = "asset"
    title = "title"  # type: ignore[assignment]  # shadows str.title(); mypy can't model str-enum members overriding mixin methods
