"""Unit tests for TranscriptSearchService."""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest
from elasticsearch import ConnectionError as ESConnectionError
from elasticsearch import Elasticsearch, TransportError

from app.services import TranscriptSearchService


class TestBuildQuery:
    """Tests for TranscriptSearchService.build_query."""

    @pytest.mark.unit
    def test_build_query_exact_mode_basic(self, test_settings) -> None:
        """build_query in exact mode creates match_phrase query with slop 0."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(
            es, index_name="test-index", config=test_settings.elasticsearch
        )

        q = svc.build_query(q="Hello World", mode="exact")

        assert "bool" in q
        b = q["bool"]
        # Must contains match_phrase with slop 0 on text.phrase
        assert any(
            "match_phrase" in m and m["match_phrase"].get("text.phrase", {}).get("slop") == 0
            for m in b.get("must", [])
        )
        # Should contains exact keyword term on text.kw
        assert any(s.get("term", {}).get("text.kw") == "hello world" for s in b.get("should", []))
        assert b.get("minimum_should_match") == 1

    @pytest.mark.unit
    def test_build_query_exact_mode_with_all_filters(self, test_settings) -> None:
        """build_query with all filters creates comprehensive query with lowercased filters."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(
            es, index_name="test-index", config=test_settings.elasticsearch
        )

        q = svc.build_query(
            q="Hello World",
            mode="exact",
            path_prefix="/Media/Path",
            path_part="Clip",
            collection="BBC",
            title_part="My Title",
            asset_id=123,
            language="EN",
        )

        assert "bool" in q
        b = q["bool"]

        # Filters lowercased (except numeric asset_id)
        filters = b.get("filter", [])
        # path_prefix -> prefix query
        assert {"prefix": {"media_path": "/media/path"}} in filters
        # path_part -> wildcard contains
        assert {"wildcard": {"media_path": "*clip*"}} in filters
        # collection -> term lowercased
        assert {"term": {"collection": "bbc"}} in filters
        # title_part -> wildcard on media_title.kw
        assert {"wildcard": {"media_title.kw": "*my title*"}} in filters
        # asset_id exact term
        assert {"term": {"asset_id": 123}} in filters
        # language -> term lowercased
        assert {"term": {"language": "en"}} in filters

    @pytest.mark.unit
    def test_build_query_exact_mode_with_single_filter(self, test_settings) -> None:
        """build_query with single filter includes only that filter."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        q = svc.build_query(q="test", mode="exact", asset_id=42)

        filters = q["bool"].get("filter", [])
        assert len(filters) == 1
        assert {"term": {"asset_id": 42}} in filters

    @pytest.mark.unit
    def test_build_query_exact_mode_no_filters(self, test_settings) -> None:
        """build_query without filters creates query without filter clause."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        q = svc.build_query(q="test", mode="exact")

        # No filter key when no filters provided
        assert "filter" not in q["bool"]

    @pytest.mark.unit
    def test_build_query_similar_mode_uses_match_and_phrase_boost(self, test_settings) -> None:
        """build_query in similar mode uses fuzzy match with phrase boosting."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        q = svc.build_query(q="fuzzy words", mode="similar")
        b = q["bool"]

        # must should have a match with fuzziness AUTO and operator and
        assert any(
            m.get("match", {}).get("text", {}).get("fuzziness") == "AUTO"
            and m["match"]["text"].get("operator") == "and"
            for m in b.get("must", [])
        )
        # should should have a phrase with slop 2 and boost 2.0
        assert any(
            s.get("match_phrase", {}).get("text.phrase", {}).get("slop") == 2
            and s["match_phrase"]["text.phrase"].get("boost") == 2.0
            for s in b.get("should", [])
        )

    @pytest.mark.unit
    def test_build_query_similar_mode_with_filters(self, test_settings) -> None:
        """build_query in similar mode supports filters."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        q = svc.build_query(q="test", mode="similar", language="fr", collection="Arte")

        filters = q["bool"].get("filter", [])
        assert {"term": {"language": "fr"}} in filters
        assert {"term": {"collection": "arte"}} in filters

    @pytest.mark.unit
    def test_build_query_lowercases_string_filters(self, test_settings) -> None:
        """build_query lowercases all string filter values for case-insensitive matching."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        q = svc.build_query(
            q="test",
            mode="exact",
            path_prefix="/UPPER/Path",
            path_part="PART",
            collection="COLLECTION",
            title_part="TITLE",
            language="EN-US",
        )

        filters = q["bool"].get("filter", [])
        assert {"prefix": {"media_path": "/upper/path"}} in filters
        assert {"wildcard": {"media_path": "*part*"}} in filters
        assert {"term": {"collection": "collection"}} in filters
        assert {"wildcard": {"media_title.kw": "*title*"}} in filters
        assert {"term": {"language": "en-us"}} in filters

    @pytest.mark.unit
    def test_build_query_asset_id_not_lowercased(self, test_settings) -> None:
        """build_query preserves numeric asset_id without transformation."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        q = svc.build_query(q="test", mode="exact", asset_id=999)

        filters = q["bool"].get("filter", [])
        assert {"term": {"asset_id": 999}} in filters


class TestSearch:
    """Tests for TranscriptSearchService.search."""

    @pytest.mark.unit
    def test_search_success_maps_hits_total_dict_and_highlights(self, test_settings) -> None:
        """search maps Elasticsearch response with dict total and highlights correctly."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, index_name="idx", config=test_settings.elasticsearch)

        es.search.return_value = {
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {
                        "_score": 1.23,
                        "_source": {
                            "asset_id": 1,
                            "segment_id": 10,
                            "language": "en",
                            "media_title": "Title A",
                            "media_path": "/m/a.mp4",
                            "start_s": 0.0,
                            "end_s": 2.5,
                            "text": "hello world",
                        },
                        "highlight": {"text": ["<em>hello</em> world"]},
                    },
                    {
                        "_score": 0.8,
                        "_source": {
                            "asset_id": 2,
                            "segment_id": 11,
                            "language": "en",
                            "media_title": "Title B",
                            "media_path": "/m/b.mp4",
                            "start_s": 5.0,
                            "end_s": 7.0,
                            "text": "lorem ipsum",
                        },
                        "highlight": {"text.phrase": ["lorem <em>ipsum</em>"]},
                    },
                ],
            }
        }

        res = svc.search(q="hello", mode="exact", size=5, offset=10)

        # Delegation to es.search with proper index and body keys
        assert es.search.called
        args, kwargs = es.search.call_args
        assert kwargs["index"] == "idx"
        body = kwargs["body"]
        assert body.get("size") == 5
        assert body.get("from") == 10
        assert "query" in body and "highlight" in body and "_source" in body

        # Response mapping
        assert res["total"] == 2
        assert isinstance(res["items"], list) and len(res["items"]) == 2
        first = res["items"][0]
        assert first["asset_id"] == 1 and first["segment_id"] == 10
        assert first["score"] == 1.23
        assert first["highlight"] == ["<em>hello</em> world"]
        second = res["items"][1]
        assert second["highlight"] == ["lorem <em>ipsum</em>"]

    @pytest.mark.unit
    def test_search_maps_total_when_int_and_no_highlights_present(self, test_settings) -> None:
        """search handles integer total and missing highlights gracefully."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {"hits": {"total": 3, "hits": [{"_source": {}, "_score": 0.1}]}}

        res = svc.search(q="x")
        assert res["total"] == 3
        assert isinstance(res["items"], list)
        assert res["items"][0]["highlight"] == []

    @pytest.mark.unit
    def test_search_success_with_defaults(self, test_settings) -> None:
        """search uses default values for size and offset when not provided."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {"hits": {"total": 0, "hits": []}}

        res = svc.search(q="test")

        body = es.search.call_args[1]["body"]
        assert body["size"] == 25
        assert body["from"] == 0
        assert res["total"] == 0
        assert res["items"] == []

    @pytest.mark.unit
    def test_search_passes_filters_to_build_query(self, test_settings) -> None:
        """search delegates all filter parameters to build_query correctly."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {"hits": {"total": 0, "hits": []}}

        svc.search(
            q="test",
            mode="similar",
            path_prefix="/media",
            path_part="clip",
            collection="bbc",
            title_part="doc",
            asset_id=7,
            language="fr",
        )

        body = es.search.call_args[1]["body"]
        query = body["query"]
        filters = query["bool"].get("filter", [])

        # Verify filters were applied
        assert {"prefix": {"media_path": "/media"}} in filters
        assert {"wildcard": {"media_path": "*clip*"}} in filters
        assert {"term": {"collection": "bbc"}} in filters
        assert {"wildcard": {"media_title.kw": "*doc*"}} in filters
        assert {"term": {"asset_id": 7}} in filters
        assert {"term": {"language": "fr"}} in filters

    @pytest.mark.unit
    def test_search_includes_highlight_configuration(self, test_settings) -> None:
        """search request includes proper highlight configuration."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {"hits": {"total": 0, "hits": []}}

        svc.search(q="test")

        body = es.search.call_args[1]["body"]
        highlight = body["highlight"]

        assert highlight["fields"]["text"]["pre_tags"] == ["<em>"]
        assert highlight["fields"]["text"]["post_tags"] == ["</em>"]
        assert highlight["fields"]["text.phrase"]["pre_tags"] == ["<em>"]
        assert highlight["fragment_size"] == 180
        assert highlight["number_of_fragments"] == 1
        assert highlight["require_field_match"] is False

    @pytest.mark.unit
    def test_search_includes_source_fields(self, test_settings) -> None:
        """search request includes specific source fields."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {"hits": {"total": 0, "hits": []}}

        svc.search(q="test")

        body = es.search.call_args[1]["body"]
        source = body["_source"]

        expected_fields = [
            "asset_id",
            "segment_id",
            "language",
            "media_title",
            "media_path",
            "start_s",
            "end_s",
            "text",
        ]
        assert source == expected_fields

    @pytest.mark.unit
    def test_search_maps_all_source_fields(self, test_settings) -> None:
        """search maps all source fields from ES response to result items."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {
            "hits": {
                "total": 1,
                "hits": [
                    {
                        "_score": 2.5,
                        "_source": {
                            "asset_id": 99,
                            "segment_id": 55,
                            "language": "de",
                            "media_title": "German Title",
                            "media_path": "/path/to/file.mp4",
                            "start_s": 10.5,
                            "end_s": 15.3,
                            "text": "sample transcript text",
                        },
                    }
                ],
            }
        }

        res = svc.search(q="sample")

        item = res["items"][0]
        assert item["asset_id"] == 99
        assert item["segment_id"] == 55
        assert item["language"] == "de"
        assert item["media_title"] == "German Title"
        assert item["media_path"] == "/path/to/file.mp4"
        assert item["start_s"] == 10.5
        assert item["end_s"] == 15.3
        assert item["text"] == "sample transcript text"
        assert item["score"] == 2.5

    @pytest.mark.unit
    def test_search_handles_missing_highlight_fields(self, test_settings) -> None:
        """search handles hits without highlight field gracefully."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {
            "hits": {
                "total": 1,
                "hits": [
                    {
                        "_score": 1.0,
                        "_source": {"asset_id": 1, "text": "test"},
                        # No highlight field
                    }
                ],
            }
        }

        res = svc.search(q="test")

        assert res["items"][0]["highlight"] == []

    @pytest.mark.unit
    def test_search_prefers_text_highlight_over_phrase(self, test_settings) -> None:
        """search prefers 'text' highlight field over 'text.phrase'."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {
            "hits": {
                "total": 1,
                "hits": [
                    {
                        "_score": 1.0,
                        "_source": {"text": "test"},
                        "highlight": {
                            "text": ["<em>primary</em>"],
                            "text.phrase": ["<em>secondary</em>"],
                        },
                    }
                ],
            }
        }

        res = svc.search(q="test")

        # Should prefer 'text' over 'text.phrase'
        assert res["items"][0]["highlight"] == ["<em>primary</em>"]

    @pytest.mark.unit
    def test_search_uses_phrase_highlight_when_text_missing(self, test_settings) -> None:
        """search falls back to 'text.phrase' highlight when 'text' is missing."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.return_value = {
            "hits": {
                "total": 1,
                "hits": [
                    {
                        "_score": 1.0,
                        "_source": {"text": "test"},
                        "highlight": {
                            "text.phrase": ["<em>phrase</em>"],
                        },
                    }
                ],
            }
        }

        res = svc.search(q="test")

        assert res["items"][0]["highlight"] == ["<em>phrase</em>"]

    @pytest.mark.unit
    def test_search_handles_es_connection_errors(self, test_settings) -> None:
        """search returns error response on Elasticsearch connection failure."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.side_effect = ESConnectionError("down")

        res = svc.search(q="x")
        assert res["total"] == 0 and res["items"] == []
        assert res["error"]["code"] == "es_unavailable"
        assert "temporarily" in res["error"]["message"].lower()
        assert "details" in res["error"]

    @pytest.mark.unit
    def test_search_handles_transport_error(self, test_settings) -> None:
        """search returns error response on Elasticsearch transport error."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, test_settings.elasticsearch)

        es.search.side_effect = TransportError(500, "boom")

        res = svc.search(q="y")
        assert res["total"] == 0 and res["items"] == []
        assert res["error"]["code"] == "es_unavailable"
        assert "message" in res["error"]
        assert "details" in res["error"]

    @pytest.mark.unit
    def test_search_handles_generic_exception(self, test_settings) -> None:
        """search returns error response on unexpected exceptions."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.side_effect = RuntimeError("boom")

        res = svc.search(q="z")
        assert res["total"] == 0 and res["items"] == []
        assert res["error"]["code"] == "search_error"
        assert "unexpected" in res["error"]["message"].lower()

    @pytest.mark.unit
    def test_search_error_response_includes_exception_details(self, test_settings) -> None:
        """search includes exception details in error response."""
        es = create_autospec(Elasticsearch, instance=True, spec_set=True)
        svc = TranscriptSearchService(es, config=test_settings.elasticsearch)

        es.search.side_effect = ValueError("specific error message")

        res = svc.search(q="test")

        assert "error" in res
        assert res["error"]["details"] == "specific error message"
