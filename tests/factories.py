# tests/factories.py
from __future__ import annotations

from datetime import UTC

import factory
from faker import Faker

from app.models.title_contents import ContentKind
from app.schemas import (
    AssetCreateInternal,
    AssetCreatePublic,
    AssetRead,
    AssetReadExtended,
    InboxItem,
    InboxItemTypeEnum,
    RunSummaryCreateInternal,
    RunSummaryCreatePublic,
    RunSummaryRead,
    ScannerRunSummaryCreateInternal,
    ScannerRunSummaryCreatePublic,
    ScannerRunSummaryRead,
    StreamCreateInternal,
    StreamRead,
    TagCreateInternal,
    TagCreatePublic,
    TagRead,
    TitleContentInsert,
    TitleContentRead,
    TitleCreateInternal,
    TitleCreatePublic,
    TitleRead,
    TitleReferenceCreatePublic,
    TitleReferenceRead,
    TitleReferenceTypeEnum,
    TitleTypeEnum,
    TransformRequestCreateInternal,
    TransformRequestRead,
    TransformRequestReadExpanded,
    IdSchemeCreateInternal,
    RunnerStateRead,
    MetadataRead,
)

fake = Faker()


class IdSchemeCreateFactory(factory.Factory):
    class Meta:
        model = IdSchemeCreateInternal

    code = factory.Sequence(lambda n: f"scheme{n}")
    label = factory.LazyAttribute(lambda obj: obj.code.upper())
    validator = None


class AssetCreateFactory(factory.Factory):
    class Meta:
        model = AssetCreateInternal

    path = factory.Faker("file_path", depth=3, extension="mp4", file_system_rule="linux")
    filename = factory.LazyAttribute(lambda obj: str(obj.path).rsplit("/", 1)[-1])
    duration = 12.34
    bitrate = 320000
    container_format = "mp4"
    size = 123456
    mtime = None
    master_asset_id = None


class AssetReadFactory(factory.Factory):
    class Meta:
        model = AssetRead

    id = factory.Faker("pyint")
    created_at = fake.date_time(tzinfo=UTC)
    duration = 12.34
    bitrate = 320000
    container_format = "mp4"
    size = 123456
    mtime = None
    master_asset_id = None
    path = factory.Faker("file_path", depth=3, extension="mp4", file_system_rule="linux")
    filename = factory.LazyAttribute(lambda obj: str(obj.path).rsplit("/", 1)[-1])


class AssetReadExtendedFactory(factory.Factory):
    class Meta:
        model = AssetReadExtended

    id = factory.Faker("pyint")
    created_at = fake.date_time(tzinfo=UTC)
    duration = 12.34
    bitrate = 320000
    container_format = "mp4"
    size = 123456
    mtime = None
    master_asset_id = None
    path = factory.Faker("file_path", depth=3, extension="mp4", file_system_rule="linux")
    filename = factory.LazyAttribute(lambda obj: str(obj.path).rsplit("/", 1)[-1])
    last_seen = None
    master_asset = None
    tags = None
    external_ids = None


class TransformRequestReadFactory(factory.Factory):
    class Meta:
        model = TransformRequestRead

    id = factory.Faker("pyint")
    created_at = factory.Faker("date_time", tzinfo=UTC)
    asset_id = factory.Faker("pyint")
    transform_type = "prefect.test"
    parameters = None
    actioned = False
    processed_at = None
    worker_notes = None
    duration = None
    outcome = None
    worker = None


class TransformRequestReadExpandedFactory(factory.Factory):
    class Meta:
        model = TransformRequestReadExpanded

    id = factory.Faker("pyint")
    created_at = factory.Faker("date_time", tzinfo=UTC)
    asset_id = factory.Faker("pyint")
    transform_type = "prefect.test"
    parameters = None
    actioned = False
    processed_at = None
    worker_notes = None
    duration = None
    outcome = None
    worker = None
    asset = AssetReadFactory()


def get_asset_creation_json(a: AssetRead | AssetReadExtended) -> dict:
    return AssetCreatePublic(
        **a.model_dump(exclude={"id", "created_at", "master_asset_id"})
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


def get_stream_creation_json(s: StreamRead) -> dict:
    return {
        "codec_type": s.codec_type,
        "codec_name": s.codec_name,
        "stream_index": s.stream_index,
        "language": s.language,
        "width": s.width,
        "height": s.height,
        "frame_rate": s.frame_rate,
    }


def get_transform_request_creation_json(t: TransformRequestRead) -> dict:
    return {
        "transform_type": t.transform_type,
        "parameters": t.parameters,
    }


def get_run_summary_creation_json(r: RunSummaryRead) -> dict:
    return RunSummaryCreatePublic(**r.model_dump(exclude={"id", "created_at"})).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


def get_scanner_run_summary_creation_json(r: ScannerRunSummaryRead) -> dict:
    return ScannerRunSummaryCreatePublic(**r.model_dump(exclude={"id", "created_at"})).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


def get_title_creation_json(t: TitleRead) -> dict:
    return TitleCreatePublic(**t.model_dump(exclude={"id"})).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


def get_title_reference_creation_json(tr: TitleReferenceRead) -> dict:
    return TitleReferenceCreatePublic(**tr.model_dump(exclude={"id", "title_id"})).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


def get_title_content_creation_json(tc: TitleContentRead) -> dict:
    return TitleContentInsert(
        **tc.model_dump(exclude={"id", "parent_title_id", "order_key"})
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


def get_tag_creation_json(t: TagRead) -> dict:
    return TagCreatePublic(
        **t.model_dump(exclude={"id", "created_at", "updated_at", "parent_id"})
    ).model_dump(mode="json", by_alias=True, exclude_none=True)


class StreamCreateFactory(factory.Factory):
    class Meta:
        model = StreamCreateInternal

    codec_type = "video"


class StreamReadFactory(factory.Factory):
    class Meta:
        model = StreamRead

    id = factory.Faker("pyint")
    asset_id = factory.Faker("pyint")
    codec_type = "video"
    codec_name = "h264"
    stream_index = factory.Faker("pyint")
    language = "eng"
    width = 1920
    height = 1080
    frame_rate = factory.Faker("pyfloat", left_digits=2, right_digits=2)


class RunSummaryFactory(factory.Factory):
    class Meta:
        model = RunSummaryCreateInternal

    worker_name = factory.Faker("name")
    worker_type = "system"
    transform_type = "prefect.transcode"
    started_at = factory.Faker("date_time", tzinfo=UTC)
    processed_count = 1
    success_count = 1
    failed_count = 0
    running_time = 1234
    extras = None


class RunSummaryReadFactory(factory.Factory):
    class Meta:
        model = RunSummaryRead

    id = factory.Faker("pyint")
    created_at = factory.Faker("date_time", tzinfo=UTC)
    worker_name = factory.Faker("name")
    worker_type = "system"
    transform_type = "prefect.transcode"
    started_at = factory.Faker("date_time", tzinfo=UTC)
    processed_count = 1
    success_count = 1
    failed_count = 0
    running_time = 1234
    extras = None


class ScannerRunSummaryFactory(factory.Factory):
    """A scan over a filesystem: every counter applies and is populated."""

    class Meta:
        model = ScannerRunSummaryCreateInternal

    worker_name = factory.Faker("name")
    worker_type = "scanner"
    scan_path = "/data/media"
    relative_to_path = "/data"
    started_at = factory.Faker("date_time", tzinfo=UTC)
    running_time = 99
    dry_run = False
    total_count = 10
    processed_count = 8
    folder_count = 2
    excluded_count = 1
    previously_seen_count = 0
    error_count = 0
    api_error_count = 0
    no_metadata_count = 1
    unsupported_file_count = 0
    extras = None


class NonFilesystemScannerRunSummaryFactory(factory.Factory):
    """A scan over something that is not a filesystem.

    Sends only what any scanner can answer, leaves the filesystem-specific
    counters at None, and puts its own counters in `extras` (media-api#37).
    """

    class Meta:
        model = ScannerRunSummaryCreateInternal

    worker_name = factory.Faker("name")
    worker_type = "scanner"
    started_at = factory.Faker("date_time", tzinfo=UTC)
    running_time = 12
    dry_run = False
    processed_count = 7
    previously_seen_count = 3
    extras = {"items_seen": 40, "created": 7, "skipped_existing": 33}


class ScannerRunSummaryReadFactory(factory.Factory):
    class Meta:
        model = ScannerRunSummaryRead

    id = factory.Faker("pyint")
    created_at = factory.Faker("date_time", tzinfo=UTC)
    worker_name = factory.Faker("name")
    worker_type = "scanner"
    scan_path = "/data/media"
    relative_to_path = "/data"
    started_at = factory.Faker("date_time", tzinfo=UTC)
    running_time = 99
    dry_run = False
    total_count = 10
    processed_count = 8
    folder_count = 2
    excluded_count = 1
    previously_seen_count = 0
    error_count = 0
    api_error_count = 0
    no_metadata_count = 1
    unsupported_file_count = 0
    extras = None


class TitleCreateFactory(factory.Factory):
    class Meta:
        model = TitleCreateInternal

    name = factory.Faker("name")
    title_type = "movie"


class TransformRequestCreateFactory(factory.Factory):
    class Meta:
        model = TransformRequestCreateInternal

    transform_type = "prefect.transcode"
    parameters = None
    actioned = False
    processed_at = None
    worker_notes = None
    duration = None
    outcome = None
    worker = None


class TitleReadFactory(factory.Factory):
    class Meta:
        model = TitleRead

    id = factory.Faker("pyint")
    name = factory.Faker("name")
    title_type = TitleTypeEnum.movie


class TitleReferenceReadFactory(factory.Factory):
    class Meta:
        model = TitleReferenceRead

    id = factory.Faker("pyint")
    title_id = factory.Faker("pyint")
    reference_type = TitleReferenceTypeEnum.article
    reference_url = "https://example.com"


class TitleContentReadFactory(factory.Factory):
    class Meta:
        model = TitleContentRead

    id = factory.Faker("pyint")
    parent_title_id = factory.Faker("pyint")
    order_key = "U"
    kind = ContentKind.asset
    child_title_id = None
    asset_id = factory.Faker("pyint")
    label = "Episode V"


class TagCreateFactory(factory.Factory):
    class Meta:
        model = TagCreateInternal

    name = factory.Faker("name")
    description = factory.Faker("text")
    color = "#000000"


class TagReadFactory(factory.Factory):
    class Meta:
        model = TagRead

    id = factory.Faker("pyint")
    name = factory.Faker("name")
    description = factory.Faker("text")
    parent_id = None
    color = "#000000"
    created_at = factory.Faker("date_time", tzinfo=UTC)
    updated_at = factory.Faker("date_time", tzinfo=UTC)


class InboxItemFactory(factory.Factory):
    class Meta:
        model = InboxItem

    path = factory.Faker("file_path", depth=3, extension="mp4", file_system_rule="linux")
    name = factory.LazyAttribute(lambda obj: str(obj.path).rsplit("/", 1)[-1])
    type = InboxItemTypeEnum.file
    size = factory.Faker("pyint")
    children = None


class RunnerStateReadFactory(factory.Factory):
    class Meta:
        model = RunnerStateRead

    runner_key = factory.Faker("uuid4")
    state = factory.LazyFunction(lambda: {"offset": 0})
    updated_at = factory.Faker("date_time", tzinfo=UTC)


class MetadataReadFactory(factory.Factory):
    class Meta:
        model = MetadataRead

    id = factory.Faker("pyint")
    asset_id = factory.Faker("pyint")
    metadata_type = factory.Faker("word")
    data = factory.LazyFunction(lambda: {"key": "value"})
    created_at = factory.Faker("date_time", tzinfo=UTC)
    updated_at = factory.Faker("date_time", tzinfo=UTC)
