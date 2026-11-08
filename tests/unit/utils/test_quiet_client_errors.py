# tests/unit/utils/test_quiet_client_errors.py
"""Unit tests for the Logfire exception callback (app/utils/logs.py).

Each test runs a small FastAPI app under Logfire's FastAPI instrumentation, with the
callback configured exactly as `setup_logging` configures it, and inspects the spans
that would have been exported.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import logfire
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from logfire.testing import TestExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from app.utils import logs
from app.utils.logs import quiet_client_errors


def _raise_not_found() -> None:
    raise HTTPException(status_code=404, detail="Not found")


@pytest.fixture()
def exporter() -> TestExporter:
    """Configure Logfire with the callback and capture spans in memory."""
    exporter = TestExporter()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
        advanced=logfire.AdvancedOptions(exception_callback=quiet_client_errors),
    )
    return exporter


@pytest.fixture()
def client(exporter: TestExporter) -> Iterator[TestClient]:
    """A minimal app whose routes raise each kind of exception."""
    router = APIRouter()

    @router.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/client-error")
    async def client_error() -> None:
        raise HTTPException(status_code=404, detail="Not found")

    @router.get("/server-error")
    async def server_error() -> None:
        raise HTTPException(status_code=500, detail="Boom")

    @router.get("/crash")
    async def crash() -> None:
        raise RuntimeError("a genuine fault")

    # Most endpoints in this codebase are plain `def`; FastAPI runs those in a
    # threadpool, and the instrumentation records their exceptions the same way.
    @router.get("/sync-ok")
    def sync_ok() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/sync-client-error")
    def sync_client_error() -> None:
        raise HTTPException(status_code=404, detail="Not found")

    @router.get("/dependency-client-error", dependencies=[Depends(_raise_not_found)])
    def dependency_client_error() -> None:
        return None

    @router.get("/no-content")
    async def no_content() -> None:
        raise HTTPException(status_code=204, detail="No tasks available")

    app = FastAPI()
    app.include_router(router)
    logfire.instrument_fastapi(app)

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _recorded_exceptions(exporter: TestExporter) -> list[str]:
    """Return the type of every exception recorded on an exported span."""
    spans = exporter.exported_spans_as_dict()
    assert spans, "nothing was exported, so an empty result would prove nothing"
    return [
        event["attributes"]["exception.type"]
        for span in spans
        for event in span.get("events", [])
        if event["name"] == "exception"
    ]


@pytest.mark.unit
@pytest.mark.api
class TestQuietClientErrors:
    """Behavioural tests for quiet_client_errors."""

    @pytest.mark.parametrize(
        "path", ["/client-error", "/sync-client-error", "/dependency-client-error"]
    )
    def test_client_error_is_returned_and_not_recorded(
        self, client: TestClient, exporter: TestExporter, path: str
    ) -> None:
        """A <500 HTTPException reaches the caller unchanged, with no exception recorded."""
        response = client.get(path)

        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}
        assert _recorded_exceptions(exporter) == []

    def test_server_error_is_still_recorded(
        self, client: TestClient, exporter: TestExporter
    ) -> None:
        """A >=500 HTTPException is recorded as before."""
        response = client.get("/server-error")

        assert response.status_code == 500
        assert _recorded_exceptions(exporter) == ["fastapi.exceptions.HTTPException"]

    def test_unexpected_exception_is_still_recorded(
        self, client: TestClient, exporter: TestExporter
    ) -> None:
        """Anything that is not an HTTPException is recorded as before."""
        response = client.get("/crash")

        assert response.status_code == 500
        assert _recorded_exceptions(exporter) == ["RuntimeError"]

    @pytest.mark.parametrize("path", ["/ok", "/sync-ok"])
    def test_success_path_is_unaffected(
        self, client: TestClient, exporter: TestExporter, path: str
    ) -> None:
        """A successful response passes through unchanged, with no exception recorded."""
        response = client.get(path)

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert _recorded_exceptions(exporter) == []

    def test_204_has_no_body_despite_detail(
        self, client: TestClient, exporter: TestExporter
    ) -> None:
        """A 204 HTTPException's `detail` does not become a body HTTP forbids."""
        response = client.get("/no-content")

        assert response.status_code == 204
        assert response.content == b""
        assert _recorded_exceptions(exporter) == []


@pytest.mark.unit
def test_setup_logging_installs_the_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The callback only works if `setup_logging` actually hands it to Logfire."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(logs.logfire, "configure", lambda **kwargs: captured.update(kwargs))

    logs.setup_logging()

    assert captured["advanced"].exception_callback is quiet_client_errors
