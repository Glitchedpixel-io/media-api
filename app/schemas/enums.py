# app/schemas/enums.py
import enum


class TransformTypeEnum(str, enum.Enum):
    """Enum for transform types"""

    extract_audio = "extract_audio"
    test = "test"
    transcribe = "transcribe"
    transcode = "transcode"
    youtube = "youtube"
    whisper_ingest = "whisper_ingest"
    clipper = "clipper"
    stream_reader = "stream_reader"
    ffprobe_metadata = "ffprobe_metadata"


class OutcomeEnum(str, enum.Enum):
    """Enum for processing outcomes"""

    cancelled = "cancelled"
    succeeded = "succeeded"
    failed = "failed"


class TitleTypeEnum(str, enum.Enum):
    """Enum for title types"""

    movie = "movie"
    tv = "tv"
    music = "music"
    audiobook = "audiobook"
    event = "event"
    collection = "collection"
    season = "season"
    other = "other"


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
