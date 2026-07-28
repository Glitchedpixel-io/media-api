# tests/unit/api/test_base_router.py
"""Unit tests for QuietClientErrorRoute (app/routers/base.py)."""

from __future__ import annotations

from collections.abc import Iterator

import logfire
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from logfire.testing import CaptureLogfire, capfire  # noqa: F401 (capfire is a fixture)

from app.routers.base import QuietClientErrorRoute


def _has_exception_event(capture: CaptureLogfire) -> bool:
    return any(
        event.name == "exception"
        for span in capture.exporter.exported_spans
        for event in span.events
    )


@pytest.fixture()
def quiet_client(capfire: CaptureLogfire) -> Iterator[TestClient]:
    """A minimal FastAPI app wired with QuietClientErrorRoute and Logfire instrumentation."""
    router = APIRouter(route_class=QuietClientErrorRoute)

    @router.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/client-error")
    async def client_error() -> None:
        raise HTTPException(status_code=404, detail="Not found")

    @router.get("/server-error")
    async def server_error() -> None:
        raise HTTPException(status_code=500, detail="Boom")

    # Most endpoints in this codebase are plain `def`, not `async def` — FastAPI
    # decides once (at route construction, before the route class swaps the
    # callable) whether to await the endpoint or dispatch it via a threadpool,
    # based on the *original* callable's sync/async-ness. These sync variants
    # guard against reintroducing a wrapper that only handles async endpoints.
    @router.get("/sync-ok")
    def sync_ok() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/sync-client-error")
    def sync_client_error() -> None:
        raise HTTPException(status_code=404, detail="Not found")

    @router.get("/no-content")
    async def no_content() -> None:
        raise HTTPException(status_code=204, detail="No tasks available")

    @router.get("/sync-no-content")
    def sync_no_content() -> None:
        raise HTTPException(status_code=204, detail="No tasks available")

    app = FastAPI()
    app.include_router(router)
    logfire.instrument_fastapi(app)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.unit
@pytest.mark.api
class TestQuietClientErrorRoute:
    """Behavioural tests for QuietClientErrorRoute."""

    def test_client_error_is_returned_and_not_recorded(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """A <500 HTTPException is converted to a JSONResponse with no exception event."""
        response = quiet_client.get("/client-error")

        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}
        assert not _has_exception_event(capfire)

    def test_server_error_is_raised_and_recorded(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """A >=500 HTTPException is re-raised and still recorded as an exception event."""
        response = quiet_client.get("/server-error")

        assert response.status_code == 500
        assert _has_exception_event(capfire)

    def test_success_path_is_unaffected(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """A successful response passes through unchanged, with no exception event."""
        response = quiet_client.get("/ok")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert not _has_exception_event(capfire)

    def test_sync_endpoint_success_is_unaffected(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """A sync (non-async def) endpoint's response is returned normally, not as a coroutine."""
        response = quiet_client.get("/sync-ok")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert not _has_exception_event(capfire)

    def test_sync_endpoint_client_error_is_returned_and_not_recorded(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """A sync endpoint's <500 HTTPException is converted to a JSONResponse, not recorded."""
        response = quiet_client.get("/sync-client-error")

        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}
        assert not _has_exception_event(capfire)

    def test_204_has_no_body_despite_detail(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """A 204 HTTPException's `detail` is dropped, not smuggled into a body HTTP forbids."""
        response = quiet_client.get("/no-content")

        assert response.status_code == 204
        assert response.content == b""
        assert not _has_exception_event(capfire)

    def test_sync_endpoint_204_has_no_body_despite_detail(
        self, quiet_client: TestClient, capfire: CaptureLogfire
    ) -> None:
        """Same no-body guarantee holds for the sync-endpoint wrapper path."""
        response = quiet_client.get("/sync-no-content")

        assert response.status_code == 204
        assert response.content == b""
        assert not _has_exception_event(capfire)
