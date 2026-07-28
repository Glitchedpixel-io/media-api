# app/search_service.py
from __future__ import annotations

from typing import Any

from elasticsearch import (
    ConnectionError as ESConnectionError,
)
from elasticsearch import (
    Elasticsearch,
    TransportError,
)

from app.config import ElasticsearchConfig


class TranscriptSearchService:
    """
    Service for searching transcript data indexed in Elasticsearch.

    Provides functionality to construct complex search queries using various filters
    and options, as well as perform searches with highlighting and error handling.

    :ivar es: The Elasticsearch client instance to be used for performing queries.
    :type es: Elasticsearch
    :ivar index: The Elasticsearch index name where transcript data is stored.
    :type index: str
    """

    def __init__(
        self, es: Elasticsearch, config: ElasticsearchConfig, index_name: str | None = None
    ) -> None:
        """
        Initializes the Elasticsearch client and determines the target index.

        This constructor is responsible for setting up the Elasticsearch client
        and specifying the index name to be used for operations. If no index
        name is provided, a default value is retrieved from the application
        settings.

        :param es: Elasticsearch client instance used to interact with the
                   Elasticsearch cluster.
        :param index_name: Name of the Elasticsearch index to be used. If None,
                           a default index name is provided by the settings.
        """
        self.es = es
        self.index = index_name or config.transcripts_index

    def build_query(
        self,
        q: str,
        mode: str = "exact",
        path_prefix: str | None = None,
        path_part: str | None = None,
        collection: str | None = None,
        title_part: str | None = None,
        asset_id: int | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """
        Constructs an Elasticsearch query based on various criteria such as text search, mode,
        filters, and additional optional parameters. The query is dynamically built to include
        match/match_phrase logic for text fields and optional filtering based on metadata like
        path, collection, asset ID, and language.

        This function is designed to support both exact match and fuzzier searches (with optional
        phrase boosting) depending on the mode specified. Filters provide additional precision
        by constraining search results to specific conditions.

        :param q: The main query text used for searching content.
        :type q: str
        :param mode: The search mode; "exact" for exact phrase matching or a general mode for
            fuzzier and more flexible matching. Defaults to "exact".
        :type mode: str
        :param path_prefix: Optional prefix for filtering results by the beginning of the
            media path. Case-insensitive.
        :type path_prefix: Optional[str]
        :param path_part: Optional part of the media path to search for. Allows wildcard-based
            searching. Case-insensitive.
        :type path_part: Optional[str]
        :param collection: Optional collection identifier for filtering results by collection name.
            Case-insensitive.
        :type collection: Optional[str]
        :param title_part: Optional fragment of the media title to search for. Allows wildcard-based
            searching. Case-insensitive.
        :type title_part: Optional[str]
        :param asset_id: Optional asset identifier for filtering results to a specific asset. Must
            be an integer.
        :type asset_id: Optional[int]
        :param language: Optional language code for filtering results based on language metadata.
            Case-insensitive.
        :type language: Optional[str]
        :return: A dictionary representing the constructed Elasticsearch query that combines
            match, phrase, and filter clauses based on the provided input parameters.
        :rtype: dict[str, Any]
        """
        must: list[dict[str, Any]] = []
        should: list[dict[str, Any]] = []
        filter_terms: list[dict[str, Any]] = []

        # Text query
        if mode == "exact":
            must.append({"match_phrase": {"text.phrase": {"query": q, "slop": 0}}})
            # Also try exact match on keyword form for very short phrases
            should.append({"term": {"text.kw": q.lower()}})
        else:
            # similar: use analyzed text with fuzziness and a phrase boost via shingles
            must.append(
                {
                    "match": {
                        "text": {
                            "query": q,
                            "operator": "and",
                            "fuzziness": "AUTO",
                        }
                    }
                }
            )
            should.append({"match_phrase": {"text.phrase": {"query": q, "slop": 2, "boost": 2.0}}})

        # Filters
        if path_prefix:
            filter_terms.append({"prefix": {"media_path": path_prefix.lower()}})
        if path_part:
            # fallback to wildcard contains on keyword (normalized lower)
            # Note: leading wildcard can be slow; allowed for flexibility.
            filter_terms.append({"wildcard": {"media_path": f"*{path_part.lower()}*"}})
        if collection:
            filter_terms.append({"term": {"collection": collection.lower()}})
        if title_part:
            # prefer keyword contains to capture exact title fragment
            filter_terms.append({"wildcard": {"media_title.kw": f"*{title_part.lower()}*"}})
        if asset_id is not None:
            filter_terms.append({"term": {"asset_id": asset_id}})
        if language:
            filter_terms.append({"term": {"language": language.lower()}})

        query: dict[str, Any] = {
            "bool": {
                "must": must if must else [{"match_all": {}}],
                **({"should": should, "minimum_should_match": 1} if should else {}),
                **({"filter": filter_terms} if filter_terms else {}),
            }
        }
        return query

    def search(
        self,
        q: str,
        mode: str = "exact",
        size: int = 25,
        offset: int = 0,
        path_prefix: str | None = None,
        path_part: str | None = None,
        collection: str | None = None,
        title_part: str | None = None,
        asset_id: int | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """
        Executes a search query against an Elasticsearch index with various filtering
        and highlighting options. This function interacts with the Elasticsearch
        client to retrieve search results and gracefully handles possible exceptions.

        :param q: The search query string to execute.
        :type q: str
        :param mode: The matching mode of the query. Defaults to "exact".
        :type mode: str
        :param size: The maximum number of results to return. Defaults to 25.
        :type size: int
        :param offset: The starting offset for pagination of results. Defaults to 0.
        :type offset: int
        :param path_prefix: An optional prefix for filtering results based on media path.
        :type path_prefix: Optional[str]
        :param path_part: An optional substring for filtering results based on media path.
        :type path_part: Optional[str]
        :param collection: An optional collection name to filter results.
        :type collection: Optional[str]
        :param title_part: An optional substring for filtering results based on media title.
        :type title_part: Optional[str]
        :param asset_id: An optional specific asset ID to narrow down the results.
        :type asset_id: Optional[int]
        :param language: An optional language code for filtering results.
        :type language: Optional[str]
        :return: A dictionary containing the search results, total count, and any
            possible errors.
        :rtype: dict[str, Any]
        """
        body = {
            "query": self.build_query(
                q=q,
                mode=mode,
                path_prefix=path_prefix,
                path_part=path_part,
                collection=collection,
                title_part=title_part,
                asset_id=asset_id,
                language=language,
            ),
            "_source": [
                "asset_id",
                "segment_id",
                "language",
                "media_title",
                "media_path",
                "start_s",
                "end_s",
                "text",
            ],
            "size": size,
            "from": offset,
            "highlight": {
                "fields": {
                    "text": {"pre_tags": ["<em>"], "post_tags": ["</em>"]},
                    "text.phrase": {"pre_tags": ["<em>"], "post_tags": ["</em>"]},
                },
                "fragment_size": 180,
                "number_of_fragments": 1,
                "require_field_match": False,
            },
        }

        try:
            resp = self.es.search(index=self.index, body=body)
        except (ESConnectionError, TransportError) as e:
            # Graceful failure with meaningful message
            message = "Search service is temporarily unavailable. Please try again later."
            details = str(e)
            return {
                "items": [],
                "total": 0,
                "error": {
                    "message": message,
                    "details": details,
                    "code": "es_unavailable",
                },
            }
        except Exception as e:
            # Catch-all safeguard
            return {
                "items": [],
                "total": 0,
                "error": {
                    "message": "An unexpected error occurred while performing search.",
                    "details": str(e),
                    "code": "search_error",
                },
            }

        hits = resp.get("hits", {})
        total_val = (
            hits.get("total", {}).get("value")
            if isinstance(hits.get("total"), dict)
            else hits.get("total")
        )
        total = int(total_val or 0)
        items: list[dict[str, Any]] = []
        for h in hits.get("hits", []):
            src = h.get("_source", {})
            hl = h.get("highlight", {})
            items.append(
                {
                    "asset_id": src.get("asset_id"),
                    "segment_id": src.get("segment_id"),
                    "language": src.get("language"),
                    "media_title": src.get("media_title"),
                    "media_path": src.get("media_path"),
                    "start_s": src.get("start_s"),
                    "end_s": src.get("end_s"),
                    "text": src.get("text"),
                    "score": h.get("_score"),
                    "highlight": hl.get("text") or hl.get("text.phrase") or [],
                }
            )
        return {"items": items, "total": total}
