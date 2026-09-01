"""The GET-only API client and the verbatim fixture writer.

Two rules are enforced here rather than left to the call sites:

* **GET only.** :meth:`FixtureCapture.get` is the only way to reach the API, so no
  case can issue a write even by accident.
* **Verbatim bytes.** A fixture is written with :meth:`pathlib.Path.write_bytes` from
  the exact body the API returned. Nothing is parsed, re-serialised, prettified,
  sorted or newline-terminated on the way to disk. Where a case needs a value out of
  a response -- a keyset cursor, an id to chain from -- it parses an in-memory copy
  via :func:`parsed`, which never touches the bytes that get written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from fastapi.testclient import TestClient

QueryParams = Mapping[str, str | int | bool | None]


@dataclass(frozen=True)
class Fixture:
    """One written fixture file, as recorded for the manifest.

    Attributes:
        filename: Path of the file relative to the output directory.
        description: What the fixture is.
        selection: How the record(s) in it were chosen.
        request: The request line that produced it.
        status: HTTP status code of the response.
        record_count: Number of records the body holds, or None if not countable.
        size_bytes: Size of the written file.
        note: Any finding worth stating alongside it.
    """

    filename: str
    description: str
    selection: str
    request: str
    status: int
    record_count: int | None
    size_bytes: int
    note: str | None = None


@dataclass(frozen=True)
class Finding:
    """A case that produced no fixture, recorded rather than omitted.

    Attributes:
        case: The case label the finding belongs to.
        description: What was looked for.
        selection: How it was looked for.
        note: Why nothing was captured.
    """

    case: str
    description: str
    selection: str
    note: str


def parsed(body: bytes) -> Any:
    """Parse a response body for inspection only.

    The result is used to chain requests and to count records. It is never written
    back to disk -- fixtures are always the original bytes.

    Args:
        body: The raw response body.

    Returns:
        Any: The decoded JSON, or None if the body is not JSON.
    """
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None


def count_records(body: bytes) -> int | None:
    """Count the records a response body holds.

    Handles the three shapes this API returns: a paginated envelope (``items``), a
    bare list, and a single object.

    Args:
        body: The raw response body.

    Returns:
        int | None: The record count, or None if the body is not JSON.
    """
    doc = parsed(body)
    if doc is None:
        return None
    if isinstance(doc, list):
        return len(doc)
    if isinstance(doc, dict):
        items = doc.get("items")
        if isinstance(items, list):
            return len(items)
        return 1
    return 1


def _request_line(path: str, params: QueryParams | None) -> str:
    """Render a request as a readable line for the manifest.

    Args:
        path: The request path.
        params: The query parameters sent, if any.

    Returns:
        str: e.g. ``GET /api/titles/?library_root=true&limit=50``.
    """
    if not params:
        return f"GET {path}"
    pairs = [f"{k}={_as_query_value(v)}" for k, v in params.items() if v is not None]
    if not pairs:
        return f"GET {path}"
    return f"GET {path}?{'&'.join(pairs)}"


def _as_query_value(value: str | int | bool | None) -> str:
    """Render a query parameter the way httpx will send it.

    Args:
        value: The parameter value.

    Returns:
        str: Its wire form -- booleans lowercased, everything else stringified.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class FixtureCapture:
    """Issues GETs against the in-process app and writes bodies verbatim.

    Args:
        client: A started TestClient wrapping the FastAPI app.
        out_dir: Directory fixtures are written under.
    """

    def __init__(self, client: TestClient, out_dir: Path) -> None:
        self._client = client
        self._out_dir = out_dir
        self.fixtures: list[Fixture] = []
        self.findings: list[Finding] = []
        self.request_count = 0

    def get(self, path: str, params: QueryParams | None = None) -> tuple[int, bytes]:
        """Issue one GET against the app.

        Args:
            path: The request path.
            params: Query parameters, with None values dropped.

        Returns:
            tuple[int, bytes]: The status code and the raw response body.
        """
        clean = {k: _as_query_value(v) for k, v in (params or {}).items() if v is not None}
        response = self._client.get(path, params=clean or None)
        self.request_count += 1
        return response.status_code, response.content

    def capture(
        self,
        filename: str,
        path: str,
        description: str,
        selection: str,
        params: QueryParams | None = None,
        note: str | None = None,
    ) -> bytes:
        """Fetch one response and write it verbatim.

        Args:
            filename: Output path relative to the fixture directory.
            path: The API path to GET.
            description: What the fixture is, for the manifest.
            selection: How the record(s) were chosen, for the manifest.
            params: Query parameters to send.
            note: Any finding to record alongside the fixture.

        Returns:
            bytes: The response body, so callers can chain off an in-memory copy.
        """
        status, body = self.get(path, params)
        target = self._out_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        # Verbatim: the exact bytes the API returned, with nothing added or removed.
        target.write_bytes(body)
        self.fixtures.append(
            Fixture(
                filename=filename,
                description=description,
                selection=selection,
                request=_request_line(path, params),
                status=status,
                record_count=count_records(body),
                size_bytes=len(body),
                note=note,
            )
        )
        return body

    def capture_each(
        self,
        directory: str,
        path_template: str,
        ids: Sequence[int],
        description: str,
        selection: str,
        note: str | None = None,
    ) -> None:
        """Fetch one record per id and write each response verbatim.

        Used where the API cannot express the query, so ids are chosen in the database
        and each record is then fetched through the API. The result is a directory of
        individual detail bodies -- not a list page.

        Args:
            directory: Output directory relative to the fixture directory.
            path_template: Path with a single ``{id}`` placeholder.
            ids: The record ids to fetch, in the order they should be written.
            description: What the set is, for the manifest.
            selection: How the ids were chosen, for the manifest.
            note: Any finding to record alongside the set.
        """
        width = max(3, len(str(len(ids))))
        for index, record_id in enumerate(ids, start=1):
            self.capture(
                filename=f"{directory}/{index:0{width}d}-{record_id}.json",
                path=path_template.format(id=record_id),
                description=f"{description} ({index} of {len(ids)})",
                selection=selection,
                note=note if index == 1 else None,
            )

    def record_finding(self, case: str, description: str, selection: str, note: str) -> None:
        """Record a case that produced no fixture.

        An empty result is itself a finding, so it is stated in the manifest rather
        than omitted from it.

        Args:
            case: The case label.
            description: What was looked for.
            selection: How it was looked for.
            note: Why nothing was captured.
        """
        self.findings.append(
            Finding(case=case, description=description, selection=selection, note=note)
        )


@dataclass
class CaseContext:
    """Everything a case function needs to run.

    Attributes:
        capture: The GET-only capture client.
        selectors: Read-only database selectors.
        max_records: Default cap on per-record fixture sets.
        totals: Measured totals, recorded for the manifest. Keys beginning with an
            underscore are working state passed between cases and are not rendered.
    """

    capture: FixtureCapture
    selectors: Any
    max_records: int
    totals: dict[str, object] = field(default_factory=dict)
