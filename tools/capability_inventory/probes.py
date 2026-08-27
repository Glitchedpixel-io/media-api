"""Phase 4 -- timed probes against a running instance.

Probes are declared in ``probes.yaml`` and validated before any request is
issued. The runner will not send a method other than GET unless that exact
``METHOD /path`` is in the file's ``allowlist``, which ships empty.

What is measured, and why each is separate:

* **p50 and p95 over N runs**, warm-up discarded. A single sample of a query
  that sometimes hits a cold cache is not a number anyone can design against.
* **Payload size in bytes**, because a response that is fast and 4 MB is not
  usable on a phone.
* **Deep pagination**, page 1 against a deep page. For the keyset endpoints the
  deep page is reached by actually following ``page.next`` cursors, since there
  is no offset to jump to -- and the interesting result is whether cost stays
  flat. For the Elasticsearch endpoint the offset is set directly, including one
  probe past ``max_result_window`` to record how that failure surfaces.
* **Time-to-first-byte separately from total** for streamed responses, plus
  Range handling, because a video scrubber cares about the former and nothing
  else.

Anything that cannot be measured is reported as ``unavailable`` with a reason.
A probe that fails is never rendered as a fast probe.
"""

from __future__ import annotations

import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ProbeResult, Timing, Unknown

BASE_URL_ENV = "CAPINV_BASE_URL"
TOKEN_ENV = "CAPINV_TOKEN"

_DEFAULT_RUNS = 5
_DEFAULT_WARMUP = 1
_DEFAULT_TIMEOUT = 30.0

# Bytes read from a streamed body before the connection is closed. Enough to
# measure time-to-first-byte and confirm the response is real, without pulling a
# whole media file across the network for every run.
_STREAM_SAMPLE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ProbeSpec:
    """One validated probe definition."""

    name: str
    method: str
    path: str
    query: dict[str, Any]
    headers: dict[str, str]
    expect_status: tuple[int, ...]
    stream: bool
    paginate: dict[str, Any] | None
    note: str | None
    runs: int
    warmup: int
    timeout: float
    records_failure_mode: bool = False
    """Set when the probe exists to record *how* something fails.

    Such a probe accepts a status meaning the endpoint did not do its work, so
    what it times is the failure path rather than the endpoint. It is reported
    like any other probe and contributes nothing to a verdict."""


@dataclass(frozen=True)
class ProbeConfig:
    """The parsed ``probes.yaml``."""

    defaults: dict[str, Any]
    allowlist: frozenset[str]
    variables: dict[str, dict[str, Any]]
    probes: tuple[ProbeSpec, ...]


class ProbeConfigError(RuntimeError):
    """Raised when ``probes.yaml`` is malformed or unsafe."""


def _normalise_allowlist(entry: object) -> str:
    """Normalise one allowlist entry to ``METHOD /path``.

    Only the method is case-folded. Upper-casing the whole entry would fold the
    path too, so a correctly-written allowlist entry would never match and an
    intentional write probe would be refused with a confusing message.
    """
    method, _, path = str(entry).strip().partition(" ")
    return f"{method.upper()} {path.strip()}".strip()


def load_config(path: Path) -> ProbeConfig:
    """Parse and validate the probe definitions.

    Args:
        path: Path to ``probes.yaml``.

    Returns:
        The validated configuration.

    Raises:
        ProbeConfigError: If the file is missing, malformed, or declares a
            non-GET probe that is not in the allowlist.
    """
    try:
        import yaml  # noqa: PLC0415 -- deferred so --skip-probes needs no parser.
    except ImportError as exc:  # pragma: no cover - PyYAML is a declared dev dep
        raise ProbeConfigError(f"PyYAML is required to read {path}: {exc}") from exc

    if not path.is_file():
        raise ProbeConfigError(f"Probe definitions not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProbeConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProbeConfigError(f"{path} must contain a mapping at the top level")

    defaults = raw.get("defaults") or {}
    allowlist = frozenset(_normalise_allowlist(entry) for entry in (raw.get("allowlist") or []))
    variables = raw.get("variables") or {}
    entries = raw.get("probes")
    if not isinstance(entries, list) or not entries:
        raise ProbeConfigError(f"{path} declares no probes")

    specs: list[ProbeSpec] = []
    names: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ProbeConfigError(f"{path}: probe #{index + 1} is not a mapping")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ProbeConfigError(f"{path}: probe #{index + 1} has no name")
        if name in names:
            raise ProbeConfigError(f"{path}: duplicate probe name {name!r}")
        names.add(name)

        probe_path = str(entry.get("path") or "").strip()
        if not probe_path.startswith("/"):
            raise ProbeConfigError(f"{path}: probe {name!r} has no absolute path")
        if "?" in probe_path:
            raise ProbeConfigError(
                f"{path}: probe {name!r} puts a query string in `path` "
                f"({probe_path!r}). Use the `query:` key instead. A path carrying a "
                "query string matches no route, so the probe would run and time "
                "successfully but its result could never be attached to an endpoint "
                "-- the endpoint would report UNKNOWN while the file looked correct."
            )

        method = str(entry.get("method") or "GET").strip().upper()
        if method != "GET" and f"{method} {probe_path}" not in allowlist:
            raise ProbeConfigError(
                f"{path}: probe {name!r} declares {method} {probe_path}, which is not "
                "a GET and is not in `allowlist`. Add it there explicitly if you "
                "really intend to send a non-read request."
            )

        expect = entry.get("expect_status", 200)
        statuses = tuple(int(s) for s in (expect if isinstance(expect, list) else [expect]))

        specs.append(
            ProbeSpec(
                name=name,
                method=method,
                path=probe_path,
                query=dict(entry.get("query") or {}),
                headers={str(k): str(v) for k, v in (entry.get("headers") or {}).items()},
                expect_status=statuses,
                stream=bool(entry.get("stream", False)),
                paginate=entry.get("paginate"),
                note=entry.get("note"),
                records_failure_mode=bool(entry.get("records_failure_mode", False)),
                runs=int(entry.get("runs", defaults.get("runs", _DEFAULT_RUNS))),
                warmup=int(entry.get("warmup", defaults.get("warmup", _DEFAULT_WARMUP))),
                timeout=float(
                    entry.get("timeout_seconds", defaults.get("timeout_seconds", _DEFAULT_TIMEOUT))
                ),
            )
        )
    return ProbeConfig(
        defaults=defaults,
        allowlist=allowlist,
        variables=variables,
        probes=tuple(specs),
    )


def resolve_base_url() -> str:
    """Read the target instance URL from the environment.

    Returns:
        The base URL with any trailing slash removed.

    Raises:
        RuntimeError: If the variable is unset.
    """
    base = os.environ.get(BASE_URL_ENV, "").strip()
    if not base:
        raise RuntimeError(
            f"{BASE_URL_ENV} is not set. Phase 4 needs a running instance, e.g.\n"
            f"    export {BASE_URL_ENV}='http://127.0.0.1:8000'\n"
            "Re-run with --skip-probes to produce a report without timings."
        )
    return base.rstrip("/")


def _pick(payload: Any, expression: str) -> Any:
    """Follow a dotted path into a decoded JSON payload.

    ``items.0.id`` walks a mapping key, then a list index, then a mapping key.

    Returns:
        The value, or None if any step does not exist.
    """
    node = payload
    for part in expression.split("."):
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


class ProbeRunner:
    """Executes the probe suite against one instance."""

    def __init__(self, base_url: str, token: str | None, verify: bool = True) -> None:
        """Open a client against the target instance.

        Args:
            base_url: Instance base URL.
            token: Bearer token, or None when the instance runs with auth
                disabled.
            verify: Whether to verify TLS certificates.

        Raises:
            RuntimeError: If httpx is unavailable.
        """
        try:
            import httpx  # noqa: PLC0415 -- deferred so --skip-probes needs no client.
        except ImportError as exc:  # pragma: no cover - httpx is a runtime dependency
            raise RuntimeError(f"httpx is required for Phase 4: {exc}") from exc

        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._httpx = httpx
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
            verify=verify,
            follow_redirects=False,
        )
        self.base_url = base_url

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def check_reachable(self) -> None:
        """Confirm the instance answers before any timing is attempted.

        Raises:
            RuntimeError: If the instance is unreachable or rejects the
                credentials. Failing here rather than per-probe means a bad
                token produces one clear error, not forty misleading ones.
        """
        try:
            response = self._client.get("/api/ping", timeout=10)
        except Exception as exc:
            raise RuntimeError(
                f"Instance at {self.base_url} is unreachable: {exc}. "
                "Start it, or re-run with --skip-probes."
            ) from exc
        if response.status_code != 200:
            raise RuntimeError(
                f"Instance at {self.base_url} answered /api/ping with "
                f"{response.status_code}; expected 200."
            )
        probe = self._client.get("/api/id_schemes", timeout=10)
        if probe.status_code in (401, 403):
            raise RuntimeError(
                f"Instance at {self.base_url} rejected the credentials on an "
                f"authenticated route ({probe.status_code}). Set {TOKEN_ENV} to a "
                "valid bearer token, or point at an instance running with "
                "AUTH_DISABLED=true."
            )

    def resolve_variables(
        self, variables: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], list[Unknown]]:
        """Resolve path variables against the live instance.

        Returns:
            A tuple of (resolved values, unknowns for anything that could not be
            resolved). A variable that fails to resolve is omitted, and every
            probe that needs it is reported as ``unavailable``.
        """
        resolved: dict[str, Any] = {}
        unknowns: list[Unknown] = []
        for name, spec in sorted(variables.items()):
            if "literal" in spec:
                resolved[name] = spec["literal"]
                continue
            endpoint = spec.get("from_endpoint")
            if not endpoint:
                unknowns.append(
                    Unknown(
                        scope="Phase 4",
                        question=f"value for probe variable `{name}`",
                        resolution="declare either `literal` or `from_endpoint` for it",
                    )
                )
                continue
            try:
                response = self._client.get(str(endpoint), params=spec.get("query") or {})
                response.raise_for_status()
                value = _pick(response.json(), str(spec.get("pick", "")))
            except Exception as exc:
                unknowns.append(
                    Unknown(
                        scope="Phase 4",
                        question=f"value for probe variable `{name}`",
                        resolution=(
                            f"resolving it from {endpoint} failed ({exc}); the probed "
                            "instance may hold no rows for that resource"
                        ),
                    )
                )
                continue
            if value is None:
                fallback = spec.get("fallback")
                if fallback is None:
                    unknowns.append(
                        Unknown(
                            scope="Phase 4",
                            question=f"value for probe variable `{name}`",
                            resolution=(
                                f"{endpoint} returned no row at `{spec.get('pick')}`; the "
                                "probed instance appears to hold no rows for that resource"
                            ),
                        )
                    )
                    continue
                value = fallback
            resolved[name] = value
        return resolved, unknowns

    # -- execution --------------------------------------------------------

    def run(self, spec: ProbeSpec, variables: dict[str, Any]) -> ProbeResult:
        """Execute one probe.

        Args:
            spec: The validated probe definition.
            variables: Resolved probe variables, substituted into both the path
                and any string query value.

        Returns:
            The result. Never raises for an ordinary probe failure -- the
            failure is the finding.
        """
        try:
            path = spec.path.format(**variables)
            query = {
                key: value.format(**variables) if isinstance(value, str) else value
                for key, value in spec.query.items()
            }
        except KeyError as exc:
            return ProbeResult(
                name=spec.name,
                endpoint_key=f"{spec.method} {spec.path}",
                method=spec.method,
                url=spec.path,
                status="unavailable",
                reason=(
                    f"probe variable {exc} could not be resolved against the probed "
                    "instance, so this probe was not run"
                ),
                notes=(spec.note,) if spec.note else (),
                records_failure_mode=spec.records_failure_mode,
            )

        notes: list[str] = [spec.note] if spec.note else []

        if spec.paginate:
            return self._run_paginated(spec, path, query, notes)
        return self._run_simple(spec, path, query, notes)

    def _run_simple(
        self,
        spec: ProbeSpec,
        path: str,
        query: dict[str, Any],
        notes: list[str],
    ) -> ProbeResult:
        """Time a single request shape."""
        key = f"{spec.method} {spec.path}"
        samples: list[float] = []
        ttfbs: list[float] = []
        size = 0
        status_code: int | None = None
        items: int | None = None

        for attempt in range(spec.warmup + spec.runs):
            try:
                if spec.stream:
                    elapsed, ttfb, size, status_code, headers = self._timed_stream(
                        spec, path, query
                    )
                    ttfbs.append(ttfb)
                    if attempt == 0:
                        notes.extend(_range_notes(headers, status_code))
                else:
                    elapsed, size, status_code, payload = self._timed_json(spec, path, query)
                    items = _count_items(payload)
            except Exception as exc:
                return ProbeResult(
                    name=spec.name,
                    endpoint_key=key,
                    method=spec.method,
                    url=_render_url(path, query),
                    status="error",
                    reason=f"{type(exc).__name__}: {exc}",
                    notes=tuple(notes),
                    records_failure_mode=spec.records_failure_mode,
                )
            if attempt >= spec.warmup:
                samples.append(elapsed)

        if status_code is not None and status_code not in spec.expect_status:
            return ProbeResult(
                name=spec.name,
                endpoint_key=key,
                method=spec.method,
                url=_render_url(path, query),
                status="error",
                http_status=status_code,
                reason=(f"expected status {list(spec.expect_status)}, got {status_code}"),
                notes=tuple(notes),
                records_failure_mode=spec.records_failure_mode,
            )

        if len(spec.expect_status) > 1 and status_code is not None:
            notes.append(
                f"responded {status_code}; the probe accepted any of "
                f"{list(spec.expect_status)}, so read the status before the timing"
            )

        return ProbeResult(
            name=spec.name,
            endpoint_key=key,
            method=spec.method,
            url=_render_url(path, query),
            status="ok",
            http_status=status_code,
            timing=_summarise(samples, ttfbs),
            bytes_=size,
            item_count=items,
            notes=tuple(notes),
            records_failure_mode=spec.records_failure_mode,
        )

    def _run_paginated(
        self,
        spec: ProbeSpec,
        path: str,
        query: dict[str, Any],
        notes: list[str],
    ) -> ProbeResult:
        """Time a deep page, reaching it the way a client would have to."""
        style = str((spec.paginate or {}).get("style", "keyset"))
        key = f"{spec.method} {spec.path}"

        if style == "offset":
            offset = int((spec.paginate or {}).get("offset", 0))
            deep_query = {**query, "offset": offset}
            notes.append(f"offset={offset} requested directly")
            return self._run_simple(
                ProbeSpec(**{**spec.__dict__, "paginate": None}), path, deep_query, notes
            )

        pages = int((spec.paginate or {}).get("pages", 20))
        cursor: str | None = None
        walked = 0
        try:
            for _ in range(pages):
                params = {**query, **({"after": cursor} if cursor else {})}
                response = self._client.get(path, params=params, timeout=spec.timeout)
                response.raise_for_status()
                payload = response.json()
                cursor = (payload.get("page") or {}).get("next")
                walked += 1
                if not cursor:
                    break
        except Exception as exc:
            return ProbeResult(
                name=spec.name,
                endpoint_key=key,
                method=spec.method,
                url=_render_url(path, query),
                status="error",
                reason=f"walking to a deep page failed after {walked} pages: {exc}",
                notes=tuple(notes),
                records_failure_mode=spec.records_failure_mode,
            )

        if cursor is None:
            return ProbeResult(
                name=spec.name,
                endpoint_key=key,
                method=spec.method,
                url=_render_url(path, query),
                status="unavailable",
                reason=(
                    f"the collection ran out after {walked} pages of "
                    f"{query.get('limit', '?')}, so there is no page "
                    f"{pages + 1} to measure on this instance"
                ),
                notes=tuple(notes),
                records_failure_mode=spec.records_failure_mode,
            )

        notes.append(
            f"deep page reached by following {walked} `page.next` cursors; a keyset "
            "endpoint offers no way to jump straight to it"
        )
        result = self._run_simple(
            ProbeSpec(**{**spec.__dict__, "paginate": None}),
            path,
            {**query, "after": cursor},
            notes,
        )
        if result.status == "ok" and result.item_count == 0:
            # The cursor was still non-null but the collection had run out, so
            # the timing measures an empty page. Reporting it next to page 1 as
            # though it were a like-for-like comparison would be a false result.
            return ProbeResult(
                **{
                    **result.__dict__,
                    "notes": result.notes
                    + (
                        f"the collection ran out before page {pages + 1}: this page "
                        "came back empty, so the timing is not comparable with page 1",
                    ),
                }
            )
        return result

    # -- timing primitives ------------------------------------------------

    def _timed_json(
        self, spec: ProbeSpec, path: str, query: dict[str, Any]
    ) -> tuple[float, int, int, Any]:
        """Issue one request and time it end to end."""
        start = time.perf_counter()
        response = self._client.get(
            path, params=query, headers=spec.headers or None, timeout=spec.timeout
        )
        body = response.content
        elapsed = (time.perf_counter() - start) * 1000
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return elapsed, len(body), response.status_code, payload

    def _timed_stream(
        self, spec: ProbeSpec, path: str, query: dict[str, Any]
    ) -> tuple[float, float, int, int, dict[str, str]]:
        """Issue one streamed request, timing first byte and sample separately."""
        start = time.perf_counter()
        first_byte: float | None = None
        read = 0
        with self._client.stream(
            "GET", path, params=query, headers=spec.headers or None, timeout=spec.timeout
        ) as response:
            headers = dict(response.headers)
            status = response.status_code
            if status >= 400:
                response.read()
                elapsed = (time.perf_counter() - start) * 1000
                return elapsed, elapsed, 0, status, headers
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if first_byte is None:
                    first_byte = (time.perf_counter() - start) * 1000
                read += len(chunk)
                if read >= _STREAM_SAMPLE_BYTES:
                    break
        elapsed = (time.perf_counter() - start) * 1000
        return elapsed, first_byte if first_byte is not None else elapsed, read, status, headers


def _summarise(samples: list[float], ttfbs: list[float]) -> Timing | None:
    """Reduce raw samples to percentiles."""
    if not samples:
        return None
    ordered = sorted(samples)
    return Timing(
        runs=len(ordered),
        p50_ms=round(statistics.median(ordered), 1),
        p95_ms=round(_percentile(ordered, 0.95), 1),
        min_ms=round(ordered[0], 1),
        max_ms=round(ordered[-1], 1),
        ttfb_p50_ms=round(statistics.median(sorted(ttfbs)), 1) if ttfbs else None,
        ttfb_p95_ms=round(_percentile(sorted(ttfbs), 0.95), 1) if ttfbs else None,
    )


def _percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already-sorted list."""
    if not ordered:
        return 0.0
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def _count_items(payload: Any) -> int | None:
    """Count the rows a response carries, for envelopes and bare lists alike."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return len(payload["items"])
    return None


def _range_notes(headers: dict[str, str], status: int) -> list[str]:
    """Describe how the server handled a Range request."""
    lower = {k.lower(): v for k, v in headers.items()}
    notes: list[str] = []
    accept = lower.get("accept-ranges")
    notes.append(f"Accept-Ranges: {accept}" if accept else "Accept-Ranges header absent")
    if status == 206:
        content_range = lower.get("content-range")
        notes.append(
            f"206 Partial Content, Content-Range: {content_range}"
            if content_range
            else "206 returned without a Content-Range header"
        )
    elif status == 416:
        notes.append(f"416 with Content-Range: {lower.get('content-range', '<absent>')}")
    return notes


def _render_url(path: str, query: dict[str, Any]) -> str:
    """Render a path and query for display, deterministically ordered."""
    if not query:
        return path
    pairs = "&".join(f"{k}={v}" for k, v in sorted(query.items()))
    return f"{path}?{pairs}"


def run_suite(
    config: ProbeConfig, base_url: str, token: str | None
) -> tuple[tuple[ProbeResult, ...], tuple[Unknown, ...]]:
    """Run every probe in the configuration.

    Args:
        config: The validated probe definitions.
        base_url: Target instance.
        token: Bearer token, or None.

    Returns:
        A tuple of (results, unknowns).

    Raises:
        RuntimeError: If the instance is unreachable or rejects credentials.
    """
    runner = ProbeRunner(base_url, token)
    try:
        runner.check_reachable()
        variables, unknowns = runner.resolve_variables(config.variables)
        results = [runner.run(spec, variables) for spec in config.probes]
        return tuple(results), tuple(unknowns)
    finally:
        runner.close()
