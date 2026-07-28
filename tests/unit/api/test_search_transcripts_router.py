# tests/unit/api/test_search_transcripts_router.py
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.app_factory import create_app
from app.auth.jwt import Principal, get_current_user
from app.config import AppConfig
from app.dependencies import get_transcript_search_service


@pytest.fixture()
def api_app_search(test_settings: AppConfig) -> Generator:
    app = create_app(test_settings)

    # bypass auth
    def _fake_user() -> Principal:
        return Principal(
            sub="test-user",
            email="test@example.com",
            preferred_username="tester",
            azp="test-client",
            roles=["user"],
            token="fake",
            token_payload={"sub": "test-user"},
        )

    app.dependency_overrides[get_current_user] = _fake_user
    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_search(api_app_search) -> Generator[TestClient, None, None]:
    with TestClient(api_app_search) as c:
        yield c


@pytest.fixture()
def transcript_search_service_mock(api_app_search) -> MagicMock:
    mock = MagicMock(name="TranscriptSearchServiceMock")
    api_app_search.dependency_overrides[get_transcript_search_service] = lambda: mock
    return mock


@pytest.mark.unit
def test_search_transcripts_success(
    client_search: TestClient, transcript_search_service_mock: MagicMock
):
    transcript_search_service_mock.search.return_value = {
        "total": 2,
        "items": [
            {
                "asset_id": 1,
                "segment_id": 10,
                "language": "en",
                "media_title": "A",
                "media_path": "media/a.mp4",
                "start_s": 0.0,
                "end_s": 1.0,
                "text": "hello",
                "highlight": ["<em>hello</em>"],
                "score": 1.23,
            },
            {
                "asset_id": 2,
                "segment_id": 20,
                "language": "en",
                "media_title": "B",
                "media_path": "media/b.mp4",
                "start_s": 2.0,
                "end_s": 3.0,
                "text": "world",
                "highlight": [],
                "score": 0.9,
            },
        ],
    }

    resp = client_search.get(
        "/api/search/transcripts",
        params={
            "q": "Hello",
            "mode": "exact",
            "size": 5,
            "offset": 10,
            "path_prefix": "Media",
            "path_part": "Clip",
            "collection": "BBC",
            "title_part": "My Title",
            "asset_id": 123,
            "language": "EN",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert isinstance(data["items"], list) and len(data["items"]) == 2

    # Ensure parameters were passed through to the service
    assert transcript_search_service_mock.search.called
    _, kwargs = transcript_search_service_mock.search.call_args
    for k, v in {
        "q": "Hello",
        "mode": "exact",
        "size": 5,
        "offset": 10,
        # Path fields and the following string fields are normalized by schema validators
        "path_prefix": "Media",
        "path_part": "Clip",
        "collection": "bbc",
        "title_part": "my title",
        "asset_id": 123,
        "language": "en",
    }.items():
        assert kwargs.get(k) == v


@pytest.mark.unit
def test_search_transcripts_es_unavailable_returns_503(
    client_search: TestClient, transcript_search_service_mock: MagicMock
):
    transcript_search_service_mock.search.return_value = {
        "total": 0,
        "items": [],
        "error": {"code": "es_unavailable", "message": "Elasticsearch is down"},
    }

    resp = client_search.get("/api/search/transcripts", params={"q": "hi"})
    assert resp.status_code == 503
    assert "down" in resp.json()["detail"].lower()


@pytest.mark.unit
def test_search_transcripts_generic_error_returns_500(
    client_search: TestClient, transcript_search_service_mock: MagicMock
):
    transcript_search_service_mock.search.return_value = {
        "total": 0,
        "items": [],
        "error": {"code": "search_error", "message": "Something bad"},
    }

    resp = client_search.get("/api/search/transcripts", params={"q": "hi"})
    assert resp.status_code == 500
    assert "something" in resp.json()["detail"].lower()
