import pytest

from app.schemas import (
    AssetCreatePublic,
    TranscriptSearchQuery,
)
from app.schemas.asset_filters import AssetFilters


@pytest.mark.unit
class TestAssetSchemas:
    def test_asset_path_is_normalized_to_posix(self):
        # Windows-style backslashes are rejected by to_linux_path, so use forward slashes input
        asset = AssetCreatePublic(
            path="C:/Videos/foo/bar.mp4",
            filename="bar.mp4",
            duration=10.5,
            bitrate=1024,
            container_format="mp4",
            size=1234,
            mtime=None,
        )
        # to_linux_path strips leading '/' and collapses dots
        assert asset.path == "C:/Videos/foo/bar.mp4".lstrip("/")

    def test_asset_path_already_posix_is_preserved(self):
        asset = AssetCreatePublic(
            path="media/movies/title/file.mp4",
            filename="file.mp4",
            duration=1.0,
            bitrate=100,
            size=10,
            mtime=None,
        )
        assert asset.path == "media/movies/title/file.mp4"

    def test_asset_path_rejects_backslashes(self):
        with pytest.raises(ValueError):
            AssetCreatePublic(
                path="a\\b\\c.mp4",
                filename="c.mp4",
                duration=1.0,
                bitrate=100,
                size=10,
                mtime=None,
            )


@pytest.mark.unit
class TestTranscriptSearchQuery:
    def test_paths_are_normalized_and_strings_lowercased(self):
        q = TranscriptSearchQuery(
            q="Hello",
            mode="exact",
            path_prefix="/Media/Movies/",
            path_part="Sub/Dir",
            collection="MyCollection",
            title_part="Some Title",
            language="EN-GB",
        )
        # path fields normalized to posix and stripped of leading '/'
        assert q.path_prefix == "Media/Movies"
        assert q.path_part == "Sub/Dir"
        # lowercase normalization
        assert q.collection == "mycollection"
        assert q.title_part == "some title"
        assert q.language == "en-gb"

    def test_none_values_are_preserved(self):
        q = TranscriptSearchQuery(q="x")
        assert q.path_prefix is None
        assert q.path_part is None
        assert q.collection is None
        assert q.title_part is None
        assert q.language is None

    def test_pagination_bounds(self):
        # Field constraints enforce non-negative offset and size between 1 and 200
        with pytest.raises(Exception):
            TranscriptSearchQuery(q="x", offset=-1)
        with pytest.raises(Exception):
            TranscriptSearchQuery(q="x", size=0)
        with pytest.raises(Exception):
            TranscriptSearchQuery(q="x", size=201)


@pytest.mark.unit
class TestApiFiltersValidators:
    def test_asset_filters_range_validators_and_path_normalization(self):
        # Valid case where max >= min
        f = AssetFilters(
            size_min=5,
            size_max=10,
            duration_min=1.5,
            duration_max=2.0,
            path_prefix="/A/B/C",
        )
        assert f.size_min == 5
        assert f.size_max == 10
        assert f.duration_min == 1.5
        assert f.duration_max == 2.0
        assert f.path_prefix == "A/B/C"

        # Invalid: size_max < size_min
        with pytest.raises(ValueError):
            AssetFilters(size_min=10, size_max=5)

        # Invalid: duration_max < duration_min
        with pytest.raises(ValueError):
            AssetFilters(duration_min=3.0, duration_max=2.5)
