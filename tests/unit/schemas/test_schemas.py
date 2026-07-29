import pytest
from datetime import datetime, UTC
from pydantic import ValidationError

from app.schemas import (
    AssetCreatePublic,
    AssetSeenBatch,
    TagCreatePublic,
    TagNameSet,
    TitleCreatePublic,
    TitleContentInsert,
    StreamCreatePublic,
    MetadataCreatePublic,
    JobCreatePublic,
    TransformRequestCreatePublic,
    TransformRequestClaim,
    TransformRequestFilters,
    TransformRequestPatchPublic,
    TitleReferenceCreatePublic,
    IdSchemeCreatePublic,
    AssetIdCreatePublic,
    InboxItem,
    InboxImportRequest,
    InboxDeleteRequest,
    TitleTypeEnum,
    TitleReferenceTypeEnum,
    OutcomeEnum,
    ContentKind,
    InboxItemTypeEnum,
)


@pytest.mark.unit
class TestAssetSchemas:
    def test_asset_create_valid(self):
        asset = AssetCreatePublic(
            path="media/movies/file.mp4",
            filename="file.mp4",
            duration=120.5,
            bitrate=2048,
            container_format="mp4",
            size=1000000,
            mtime=datetime.now(UTC),
        )
        assert asset.path == "media/movies/file.mp4"
        assert asset.duration == 120.5

    def test_asset_seen_batch_valid(self):
        batch = AssetSeenBatch(ids=[1, 2, 3])
        assert len(batch.ids) == 3

    def test_asset_seen_batch_empty_list_fails(self):
        with pytest.raises(ValidationError):
            AssetSeenBatch(ids=[])

    def test_asset_time_since_last_seen_with_none(self):
        asset = AssetCreatePublic(
            path="test.mp4",
            filename="test.mp4",
            duration=1.0,
            bitrate=100,
            size=100,
            mtime=None,
            last_seen=None,
        )
        # When last_seen is None, should return timedelta.max
        from datetime import timedelta

        assert asset.time_since_last_seen == timedelta.max


@pytest.mark.unit
class TestTagSchemas:
    def test_tag_create_valid(self):
        tag = TagCreatePublic(name="Action", description="Action movies", color="#FF0000")
        assert tag.name == "action"  # Normalized to lowercase
        assert tag.color == "#FF0000"

    def test_tag_name_empty_fails(self):
        with pytest.raises(ValidationError, match="Tag name cannot be empty"):
            TagCreatePublic(name="", color="#FF0000")

    def test_tag_name_whitespace_only_fails(self):
        with pytest.raises(ValidationError, match="Tag name cannot be empty"):
            TagCreatePublic(name="   ", color="#FF0000")

    def test_tag_name_normalized_to_lowercase(self):
        tag = TagCreatePublic(name="ACTION", color="#FF0000")
        assert tag.name == "action"

    def test_tag_color_default(self):
        tag = TagCreatePublic(name="test")
        assert tag.color == "#6B7280"

    def test_tag_color_invalid_format_fails(self):
        with pytest.raises(ValidationError):
            TagCreatePublic(name="test", color="red")

    def test_tag_color_wrong_length_fails(self):
        with pytest.raises(ValidationError):
            TagCreatePublic(name="test", color="#FF")

    def test_tag_name_max_length_exceeded_fails(self):
        with pytest.raises(ValidationError):
            TagCreatePublic(name="x" * 51, color="#FF0000")

    def test_tag_description_max_length_exceeded_fails(self):
        with pytest.raises(ValidationError):
            TagCreatePublic(name="test", description="x" * 256, color="#FF0000")

    def test_tag_name_set_normalizes_to_lowercase(self):
        tag_set = TagNameSet(tag_names=["Action", "DRAMA", "Comedy"])
        assert tag_set.tag_names == ["action", "drama", "comedy"]

    def test_tag_name_set_auto_create_default(self):
        tag_set = TagNameSet(tag_names=["test"])
        assert tag_set.auto_tag_create is True


@pytest.mark.unit
class TestTitleSchemas:
    def test_title_create_valid(self):
        title = TitleCreatePublic(
            name="The Matrix",
            title_type=TitleTypeEnum.movie,
            release_year=1999,
            synopsis="A hacker discovers reality",
        )
        assert title.name == "The Matrix"
        assert title.title_type == TitleTypeEnum.movie

    def test_title_create_minimal(self):
        title = TitleCreatePublic(name="Test", title_type=TitleTypeEnum.other)
        assert title.name == "Test"
        assert title.release_year is None
        assert title.synopsis is None

    def test_title_type_enum_values(self):
        assert TitleTypeEnum.movie.value == "movie"
        assert TitleTypeEnum.tv.value == "tv"
        assert TitleTypeEnum.audiobook.value == "audiobook"


@pytest.mark.unit
class TestTitleContentSchemas:
    def test_title_content_with_asset(self):
        content = TitleContentInsert(kind=ContentKind.asset, asset_id=1, label="Main Feature")
        assert content.kind == ContentKind.asset
        assert content.asset_id == 1
        assert content.child_title_id is None

    def test_title_content_with_child_title(self):
        content = TitleContentInsert(kind=ContentKind.title, child_title_id=2, label="Season 1")
        assert content.kind == ContentKind.title
        assert content.child_title_id == 2
        assert content.asset_id is None


@pytest.mark.unit
class TestStreamSchemas:
    def test_stream_create_video(self):
        stream = StreamCreatePublic(
            stream_index=0,
            codec_type="video",
            codec_name="h264",
            width=1920,
            height=1080,
            frame_rate=23.976,
        )
        assert stream.codec_type == "video"
        assert stream.width == 1920

    def test_stream_create_audio(self):
        stream = StreamCreatePublic(
            stream_index=1,
            codec_type="audio",
            codec_name="aac",
            channels=2,
            sample_rate=48000,
            language="eng",
        )
        assert stream.codec_type == "audio"
        assert stream.channels == 2

    def test_stream_create_minimal(self):
        stream = StreamCreatePublic(codec_type="subtitle")
        assert stream.codec_type == "subtitle"
        assert stream.codec_name is None


@pytest.mark.unit
class TestMetadataSchemas:
    def test_metadata_create_valid(self):
        metadata = MetadataCreatePublic(
            metadata_type="ffprobe", data={"format": "mp4", "duration": 120.5}
        )
        assert metadata.metadata_type == "ffprobe"
        assert metadata.data["format"] == "mp4"

    def test_metadata_empty_dict_allowed(self):
        metadata = MetadataCreatePublic(metadata_type="custom", data={})
        assert metadata.data == {}


@pytest.mark.unit
class TestJobSchemas:
    def test_job_create_valid(self):
        job = JobCreatePublic(job_key="scanner-001")
        assert job.job_key == "scanner-001"


@pytest.mark.unit
class TestTransformRequestSchemas:
    def test_transform_request_create_minimal(self):
        tr = TransformRequestCreatePublic(transform_type="prefect.transcribe")
        assert tr.transform_type == "prefect.transcribe"
        assert tr.actioned is False

    def test_transform_request_create_with_parameters(self):
        tr = TransformRequestCreatePublic(
            transform_type="prefect.extract_audio",
            parameters={"format": "mp3", "bitrate": 128},
        )
        assert tr.parameters == {"format": "mp3", "bitrate": 128}

    def test_transform_request_claim_valid(self):
        claim = TransformRequestClaim(
            transform_type="prefect.transcribe",
            worker="worker-001",
            external_job_id="job-123",
        )
        assert claim.worker == "worker-001"

    def test_outcome_enum_values(self):
        assert OutcomeEnum.succeeded.value == "succeeded"
        assert OutcomeEnum.failed.value == "failed"
        assert OutcomeEnum.cancelled.value == "cancelled"


@pytest.mark.unit
class TestTransformRoutingKeyValidation:
    """Shape-only validation of the provider-qualified transform_type routing key.

    Accept: `<provider>.<anything-nonempty-and-non-whitespace>`, remainder may
    itself contain dots. Reject: no dot, empty provider, empty local type,
    empty/blank string, or any whitespace (including a trailing newline --
    pydantic-core's default rust-regex engine treats `$` as strict
    end-of-haystack, unlike Python's `re`).
    """

    VALID = [
        "prefect.transcode",
        "webhook.thumbnail.generate",
        "prefect.media/transcode",
    ]
    INVALID = [
        "prefect",
        ".transcode",
        "prefect.",
        "",
        "   ",
        "prefect. transcode",
        " prefect.transcode",
        "prefect.transcode ",
        "prefect .transcode",
        "prefect.transcode\t",
        "prefect.transcode\n",
    ]

    @pytest.mark.parametrize("value", VALID)
    def test_create_public_accepts_valid_key(self, value):
        tr = TransformRequestCreatePublic(transform_type=value)
        assert tr.transform_type == value

    @pytest.mark.parametrize("value", INVALID)
    def test_create_public_rejects_invalid_key(self, value):
        with pytest.raises(ValidationError) as exc_info:
            TransformRequestCreatePublic(transform_type=value)
        assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"

    @pytest.mark.parametrize("value", VALID)
    def test_claim_accepts_valid_key(self, value):
        claim = TransformRequestClaim(transform_type=value, worker="w")
        assert claim.transform_type == value

    @pytest.mark.parametrize("value", INVALID)
    def test_claim_rejects_invalid_key(self, value):
        with pytest.raises(ValidationError) as exc_info:
            TransformRequestClaim(transform_type=value, worker="w")
        assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"

    @pytest.mark.parametrize("value", VALID)
    def test_patch_public_accepts_valid_key(self, value):
        patch = TransformRequestPatchPublic(transform_type=value)
        assert patch.transform_type == value

    @pytest.mark.parametrize("value", INVALID)
    def test_patch_public_rejects_invalid_key(self, value):
        with pytest.raises(ValidationError) as exc_info:
            TransformRequestPatchPublic(transform_type=value)
        assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"

    def test_patch_public_omitted_transform_type_is_none(self):
        patch = TransformRequestPatchPublic()
        assert patch.transform_type is None

    @pytest.mark.parametrize("value", VALID)
    def test_filters_accepts_valid_key(self, value):
        filters = TransformRequestFilters(transform_type=value)
        assert filters.transform_type == value

    @pytest.mark.parametrize("value", INVALID)
    def test_filters_rejects_invalid_key(self, value):
        with pytest.raises(ValidationError) as exc_info:
            TransformRequestFilters(transform_type=value)
        assert exc_info.value.errors()[0]["type"] == "string_pattern_mismatch"

    def test_filters_omitted_transform_type_is_none(self):
        filters = TransformRequestFilters()
        assert filters.transform_type is None


@pytest.mark.unit
class TestTitleReferenceSchemas:
    def test_title_reference_create_valid(self):
        ref = TitleReferenceCreatePublic(
            reference_type=TitleReferenceTypeEnum.review,
            reference_url="https://example.com/review",
            label="Great Review",
        )
        assert ref.reference_type == TitleReferenceTypeEnum.review
        assert ref.reference_url == "https://example.com/review"

    def test_title_reference_minimal(self):
        ref = TitleReferenceCreatePublic(
            reference_type=TitleReferenceTypeEnum.metadata,
            reference_url="https://example.com/metadata",
        )
        assert ref.label is None


@pytest.mark.unit
class TestIdSchemeSchemas:
    def test_id_scheme_create_valid(self):
        scheme = IdSchemeCreatePublic(code="imdb", label="IMDb ID", validator=r"^tt\d{7,8}$")
        assert scheme.code == "imdb"
        assert scheme.label == "IMDb ID"

    def test_id_scheme_without_validator(self):
        scheme = IdSchemeCreatePublic(code="custom", label="Custom ID")
        assert scheme.validator is None

    def test_asset_id_create_valid(self):
        asset_id = AssetIdCreatePublic(scheme_id=1, external_id="tt1234567")
        assert asset_id.scheme_id == 1
        assert asset_id.external_id == "tt1234567"


@pytest.mark.unit
class TestInboxSchemas:
    def test_inbox_item_file(self):
        item = InboxItem(
            path="uploads/video.mp4",
            name="video.mp4",
            type=InboxItemTypeEnum.file,
            size=1000000,
        )
        assert item.type == InboxItemTypeEnum.file
        assert item.size == 1000000

    def test_inbox_item_directory(self):
        item = InboxItem(
            path="uploads/folder",
            name="folder",
            type=InboxItemTypeEnum.dir,
            children=[],
        )
        assert item.type == InboxItemTypeEnum.dir
        assert item.children == []

    def test_inbox_item_path_normalized(self):
        item = InboxItem(
            path="/uploads/video.mp4",
            name="video.mp4",
            type=InboxItemTypeEnum.file,
            size=100,
        )
        assert item.path == "uploads/video.mp4"

    def test_inbox_import_request_valid(self):
        req = InboxImportRequest(source="inbox/file.mp4", target="media/file.mp4")
        assert req.source == "inbox/file.mp4"
        assert req.target == "media/file.mp4"

    def test_inbox_import_request_path_normalized(self):
        req = InboxImportRequest(source="/inbox/file.mp4", target="/media/file.mp4")
        assert req.source == "inbox/file.mp4"
        assert req.target == "media/file.mp4"

    def test_inbox_delete_request_valid(self):
        req = InboxDeleteRequest(source="inbox/old.mp4")
        assert req.source == "inbox/old.mp4"

    def test_inbox_delete_request_path_normalized(self):
        req = InboxDeleteRequest(source="/inbox/old.mp4")
        assert req.source == "inbox/old.mp4"
