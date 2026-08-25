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


class EntityTypeEnum(str, enum.Enum):
    """Enum for external identifier entity types"""

    asset = "asset"
    title = "title"  # type: ignore[assignment]  # shadows str.title(); mypy can't model str-enum members overriding mixin methods
